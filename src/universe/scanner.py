"""
Сканер вселенной монет.

UniverseScanner — разовый глубокий скан:
  1. Собирает все фьючерсные инструменты (Bybit/Binance/MEXC).
  2. Фильтрует по ликвидности (оборот за 24ч ≥ GEM_MIN_VOLUME_USD).
  3. Добавляет источники «скрытых» монет: CoinGecko тренды/муверы, новости.
  4. Быстро оценивает всех кандидатов, топ-N анализирует глубоко.
  5. Сохраняет найденные монеты (gems) и продвигает их в наблюдение.

WatchlistEngine — фоновая петля наблюдения за списком монет:
  свежие анализы, алерты, обновление позиций по стопам/целям.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from src.analysis.engine import AnalysisEngine, AnalysisResult
from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.store import Store
from src.core.timeutil import now_ms
from src.data.collector import MarketDataSource
from src.data.models import Instrument, Ticker

logger = get_logger("universe.scanner")

MAJOR_BASES = {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "LTC", "TRX", "AVAX", "LINK", "DOT", "TON"}


@dataclass
class ScanReport:
    ts_ms: int
    mode: str
    total_instruments: int
    candidates: int
    analyzed: int
    gems: list[dict] = field(default_factory=list)
    duration_sec: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ts_ms": self.ts_ms,
            "mode": self.mode,
            "total_instruments": self.total_instruments,
            "candidates": self.candidates,
            "analyzed": self.analyzed,
            "gems": self.gems,
            "duration_sec": round(self.duration_sec, 1),
        }


class UniverseScanner:
    def __init__(self, source: MarketDataSource, engine: AnalysisEngine, settings: Settings, store: Store):
        self.source = source
        self.engine = engine
        self.settings = settings
        self.store = store
        self._last_scan_ts = 0.0
        self._scanning = False
        self._last_report: ScanReport | None = None

    # ── Быстрая оценка кандидата ──
    async def quick_score(self, ticker: Ticker, is_major: bool) -> float:
        """Грубая оценка 0–100 на основе тикерных данных (без свечей)."""
        score = 0.0
        pct = abs(ticker.price_24h_pct)
        score += min(20.0, pct * 2.0)  # движение за 24ч
        if ticker.turnover_24h >= self.settings.GEM_MIN_VOLUME_USD:
            score += 20.0
        else:
            score += 20.0 * (ticker.turnover_24h / self.settings.GEM_MIN_VOLUME_USD)
        if ticker.funding_rate is not None:
            f = max(-0.002, min(0.002, ticker.funding_rate))
            score += 5.0 * (1.0 - abs(f) / 0.002)
        if not is_major:
            score += 12.0
        return round(score, 1)

    async def scan(self, deep_top: int | None = None) -> ScanReport:
        if self._scanning:
            logger.info("Скан уже выполняется — пропускаю")
            if self._last_report:
                return self._last_report
            return ScanReport(ts_ms=now_ms(), mode=self.source.name, total_instruments=0, candidates=0, analyzed=0)
        self._scanning = True
        t0 = time.time()
        deep_top = deep_top or self.settings.DEEP_ANALYZE_TOP
        try:
            report = await self._scan_inner(deep_top)
            self._last_report = report
            self._last_scan_ts = time.time()
            return report
        finally:
            self._scanning = False

    async def _scan_inner(self, deep_top: int) -> ScanReport:
        t0 = time.time()
        # 1. Инструменты
        try:
            instruments = await self.source.discover_instruments()
        except Exception as e:  # noqa: BLE001
            logger.error("Не удалось получить список инструментов: %s", e)
            return ScanReport(ts_ms=now_ms(), mode=self.source.name, total_instruments=0, candidates=0, analyzed=0)
        symbols = [i.symbol for i in instruments if i.status == "Trading"]
        if not symbols:
            symbols = [i.symbol for i in instruments]
        logger.info("Инструментов: %d", len(symbols))

        # 2. Тикеры всех инструментов
        try:
            tickers = await self.source.get_tickers()
        except Exception as e:  # noqa: BLE001
            logger.error("Тикеры недоступны: %s", e)
            tickers = []
        ticker_map = {t.symbol: t for t in tickers}

        # 3. Кандидаты: фьючерсные + спотовые муверы + тренды
        candidates: dict[str, tuple[Ticker | None, float]] = {}  # symbol -> (ticker, quick_score)

        for sym, t in list(ticker_map.items()):
            base = sym.replace("USDT", "")
            if base.endswith("1000"):
                base = base[4:]
            is_major = base in MAJOR_BASES
            if not t.turnover_24h or t.turnover_24h < self.settings.GEM_MIN_VOLUME_USD * 0.1:
                continue
            if t.price_24h_pct == 0 and t.turnover_24h == 0:
                continue
            qs = await self.quick_score(t, is_major)
            candidates[sym] = (t, qs)

        # спотовые муверы CoinGecko
        try:
            movers = await self.source.get_spot_movers(25)
            for m in movers:
                base = m.symbol.replace("USDT", "")
                if base.endswith("1000"):
                    base = base[4:]
                is_major = base in MAJOR_BASES or m.rank <= 15
                qs = await self.quick_score(
                    Ticker(
                        symbol=m.symbol, last=0, price_24h_pct=m.price_24h_pct,
                        turnover_24h=m.volume_24h, volume_24h=m.volume_24h,
                    ),
                    is_major,
                )
                cur = candidates.get(m.symbol)
                if cur is None or qs + 8 > cur[1]:
                    candidates[m.symbol] = (
                        cur[0] if cur else None,
                        qs + 8,
                    )
        except Exception:  # noqa: BLE001
            pass

        # тренды CoinGecko
        try:
            trending = await self.source.get_trending(12)
            for m in trending:
                base = m.symbol.replace("USDT", "")
                if base.endswith("1000"):
                    base = base[4:]
                is_major = base in MAJOR_BASES or m.rank <= 15
                qs = 40.0 if not is_major else 25.0
                cur = candidates.get(m.symbol)
                if cur is None or qs + 10 > cur[1]:
                    candidates[m.symbol] = (cur[0] if cur else None, qs + 10)
        except Exception:  # noqa: BLE001
            pass

        # монеты из свежих новостей
        try:
            news = await self.source.get_news(20)
            for n in news:
                for sym in n.symbols[:3]:
                    sym = sym.upper()
                    if not sym.endswith("USDT"):
                        sym += "USDT"
                    base = sym.replace("USDT", "")
                    if base.endswith("1000"):
                        base = base[4:]
                    is_major = base in MAJOR_BASES
                    bonus = 12.0 * n.sentiment if n.sentiment > 0 else 0.0
                    cur = candidates.get(sym)
                    if cur is None or 35.0 + bonus > cur[1]:
                        candidates[sym] = (cur[0] if cur else None, 35.0 + bonus)
        except Exception:  # noqa: BLE001
            pass

        # 4. Сортировка и глубокий анализ топ-N
        ranked = sorted(candidates.items(), key=lambda kv: kv[1][1], reverse=True)
        gems: list[dict] = []
        analyzed = 0
        deep_symbols = [sym for sym, _ in ranked[: deep_top * 2]]

        async def _deep(sym: str) -> AnalysisResult | None:
            try:
                return await self.engine.analyze(sym)
            except Exception:  # noqa: BLE001
                return None

        results = await asyncio.gather(*(_deep(s) for s in deep_symbols))
        for sym, res in zip(deep_symbols, results):
            if res is None:
                continue
            analyzed += 1
            t, qs = candidates[sym]
            if res.score >= self.settings.GEM_MIN_SCORE and (
                not MAJOR_BASES.intersection({sym.replace("USDT", "")})
                or res.verdict in ("STRONG_BUY", "BUY")
            ):
                gems.append(
                    {
                        "symbol": sym,
                        "source": "scanner",
                        "score": res.score,
                        "tier": res.tier,
                        "direction": res.direction,
                        "price": res.price,
                        "price_24h_pct": res.price_24h_pct,
                        "turnover_24h": res.turnover_24h,
                        "verdict": res.verdict,
                        "reason": res.reasons[0] if res.reasons else res.recommendation,
                    }
                )
            # стоп после топ-15 глубоких
            if analyzed >= deep_top:
                break

        gems.sort(key=lambda g: g["score"], reverse=True)
        if gems:
            self.store.save_gems(now_ms(), gems)
            await self._promote_to_watchlist(gems)

        report = ScanReport(
            ts_ms=now_ms(),
            mode=self.source.name,
            total_instruments=len(symbols),
            candidates=len(candidates),
            analyzed=analyzed,
            gems=gems,
            duration_sec=time.time() - t0,
        )
        logger.info(
            "Скан завершён: инструментов=%d кандидатов=%d глубоких=%d находок=%d (%.1fs)",
            report.total_instruments, report.candidates, report.analyzed, len(gems), report.duration_sec,
        )
        return report

    async def _promote_to_watchlist(self, gems: list[dict]) -> None:
        """Автодобавление сильных находок в наблюдение (с лимитом)."""
        try:
            from src.core.context import get_context

            watch = get_context().watcher
            if watch is None:
                return
            current = list(watch.watchlist)
            for g in gems:
                if g["score"] >= self.settings.GEM_PROMOTE_MIN_SCORE and g["symbol"] not in current:
                    if len(current) >= self.settings.WATCH_MAX_SYMBOLS:
                        break
                    watch.add_symbol(g["symbol"])
                    current.append(g["symbol"])
                    logger.info("Добавлена в наблюдение: %s (score %.0f)", g["symbol"], g["score"])
        except Exception:  # noqa: BLE001
            pass


class WatchlistEngine:
    """Фоновое наблюдение: анализ, алерты, сопровождение позиций."""

    def __init__(self, source: MarketDataSource, engine: AnalysisEngine, settings: Settings, store: Store):
        self.source = source
        self.engine = engine
        self.settings = settings
        self.store = store
        self.watchlist: list[str] = list(settings.watchlist)
        self.results: dict[str, AnalysisResult] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_cycle_ms = 0

    def add_symbol(self, symbol: str) -> None:
        symbol = symbol.upper()
        if symbol not in self.watchlist:
            self.watchlist.append(symbol)

    def remove_symbol(self, symbol: str) -> None:
        self.watchlist = [s for s in self.watchlist if s != symbol.upper()]

    def get_results(self) -> list[AnalysisResult]:
        out = [self.results[s] for s in self.watchlist if s in self.results]
        out.sort(key=lambda r: r.score, reverse=True)
        return out

    def _target_hit(self, plan, price: float) -> bool:
        if plan.direction == "LONG":
            return price >= plan.targets[0]
        return price <= plan.targets[0]

    def _stop_hit(self, plan, price: float) -> bool:
        if plan.direction == "LONG":
            return price <= plan.stop_loss
        return price >= plan.stop_loss

    async def _check_positions(self, results: list[AnalysisResult]) -> list[dict]:
        """Сопровождение бумажных позиций по стопам и целям."""
        events: list[dict] = []
        for res in results:
            if res.plan is None or res.direction not in ("LONG", "SHORT"):
                continue
            opened = self.store.positions("open")
            has_open = any(p["symbol"] == res.symbol for p in opened)
            if not has_open:
                continue
            price = res.price
            if self._stop_hit(res.plan, price):
                self.store.close_position(res.symbol, price, "стоп-лосс")
                events.append({"symbol": res.symbol, "event": "stop_loss", "price": price})
                logger.info("Стоп-лосс сработал: %s @ %.8g", res.symbol, price)
            elif self._target_hit(res.plan, price):
                self.store.close_position(res.symbol, price, "цель 1 достигнута")
                events.append({"symbol": res.symbol, "event": "target_1", "price": price})
                logger.info("Цель 1 достигнута: %s @ %.8g", res.symbol, price)
        return events

    async def run_cycle(self, notify=None) -> list[dict]:
        """Один цикл наблюдения. Возвращает новые алерты."""
        results: list[AnalysisResult] = []
        symbols = list(self.watchlist)
        sem = asyncio.Semaphore(6)

        async def _one(sym: str) -> AnalysisResult | None:
            async with sem:
                try:
                    return await self.engine.analyze(sym)
                except Exception as e:  # noqa: BLE001
                    logger.debug("Наблюдение %s: %s", sym, e)
                    return None

        out = await asyncio.gather(*(_one(s) for s in symbols))
        for sym, res in zip(symbols, out):
            if res is not None:
                results.append(res)
                self.results[sym] = res

        alerts: list[dict] = []
        for res in results:
            if res.score >= self.settings.ALERT_MIN_SCORE and res.direction in ("LONG", "SHORT"):
                if self._should_alert(res):
                    alerts.append(res.to_dict())
                    self.store.save_signal(
                        res.ts_ms, res.symbol, res.direction, res.score, res.tier, res.to_dict()
                    )
        pos_events = await self._check_positions(results)
        self.last_cycle_ms = now_ms()

        if notify and alerts:
            try:
                await notify(alerts, pos_events)
            except Exception as e:  # noqa: BLE001
                logger.warning("Уведомление не отправлено: %s", e)
        return alerts

    def _should_alert(self, res: AnalysisResult) -> bool:
        """Не спамим одним и тем же сигналом чаще раза в час."""
        recent = self.store.recent_signals(limit=5, symbol=res.symbol)
        if not recent:
            return True
        last = recent[0]
        if now_ms() - int(last["ts_ms"]) < 3_600_000:
            return False
        return True

    async def start(self, notify=None) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(notify))

    async def _loop(self, notify=None) -> None:
        logger.info("Наблюдение запущено: %d монет, интервал %dс", len(self.watchlist), self.settings.WATCH_INTERVAL_SECONDS)
        while not self._stop.is_set():
            try:
                await self.run_cycle(notify)
            except Exception as e:  # noqa: BLE001
                logger.exception("Ошибка цикла наблюдения: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.WATCH_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
