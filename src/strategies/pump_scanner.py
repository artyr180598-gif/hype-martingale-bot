from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)

BYBIT_REST = "https://api.bybit.com"
BYBIT_TICKER_URL = f"{BYBIT_REST}/v5/market/tickers?category=linear"
BYBIT_ORDERBOOK_URL = f"{BYBIT_REST}/v5/market/orderbook?category=linear&symbol={{symbol}}&limit=50"
BYBIT_KLINE_URL = f"{BYBIT_REST}/v5/market/kline?category=linear&symbol={{symbol}}&interval={{interval}}&limit=100"
BYBIT_INSTRUMENT_URL = f"{BYBIT_REST}/v5/market/instruments-info?category=linear&limit=1000"
BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"

SETTINGS_PATH = Path("data/pump_settings.json")


@dataclass
class PumpSettings:
    interval_seconds: int = 300
    threshold_pct: float = 5.0
    rsi_enabled: bool = False
    rsi_timeframes: tuple[str, ...] = ("5", "15", "60")
    rsi_overbought: float = 80.0
    rsi_oversold: float = 20.0
    day_filter_enabled: bool = False
    day_min_pct: float = 0.0
    signal_types: str = "BOTH"
    show_imbalance: bool = True
    show_listing: bool = True
    show_hashtag: bool = True
    show_volume: bool = True
    show_volume_spike: bool = True
    show_funding: bool = True
    show_oi: bool = True
    confirmation_timeframe: str = "1"
    confirmation_candles: int = 3
    cooldown_seconds: int = 300

    @classmethod
    def load(cls) -> "PumpSettings":
        try:
            raw = json.loads(SETTINGS_PATH.read_text())
            values = asdict(cls())
            values.update(raw)
            values["rsi_timeframes"] = tuple(values["rsi_timeframes"])
            return cls(**values)
        except Exception:
            return cls()

    def save(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2))


@dataclass
class PumpSignal:
    symbol: str
    direction: str
    change_pct: float
    start_price: float
    current_price: float
    day_pct: float
    imbalance_buy_pct: float | None
    volume_24h: float
    volume_spike: float | None
    funding_rate: float | None
    open_interest: float | None
    open_interest_change_pct: float | None
    listing_ms: int | None
    rsi: dict[str, float]
    score: int
    trade_action: str
    trade_reason: str
    entry_low: float | None
    entry_high: float | None
    stop_price: float | None
    tp1: float | None
    tp2: float | None
    ts: float


class PumpScanner:
    """Real-time Bybit Pump/Dump scanner.

    It does not place orders. The first two filters are always active:
    rolling monitoring interval and price-change threshold. Optional filters
    only remove candidates when explicitly enabled.
    """

    def __init__(self) -> None:
        self.settings = PumpSettings.load()
        self.session: aiohttp.ClientSession | None = None
        self.prices: dict[str, deque[tuple[float, float]]] = {}
        self.ticker_cache: dict[str, dict] = {}
        self.last_trigger: dict[tuple[str, str], float] = {}
        self.listings: dict[str, int] = {}
        self.prev_oi: dict[str, float] = {}
        self.running = False
        self.ws_tasks: list[asyncio.Task] = []
        self.ws_symbols: list[str] = []
        self.ws_ready = False
        self._history_seed_task: asyncio.Task | None = None
        self.history_ready = False
        self.last_diagnostic = 0.0
        self.diagnostic_interval = 30.0

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        await self._load_listings()
        self.running = True
        self._history_seed_task = asyncio.create_task(self._seed_history(), name="pump-history-seed")
        # WebSocket is the primary ticker feed; REST remains a recovery path.
        self.ws_tasks = [
            asyncio.create_task(
                self._ticker_ws_chunk(chunk), name=f"bybit-ticker-{i}"
            )
            for i, chunk in enumerate(self._chunks(self.ws_symbols, 100))
        ]
        log.info("Pump scanner started for %d Bybit linear USDT symbols", len(self.ws_symbols))

    async def stop(self) -> None:
        self.running = False
        for task in self.ws_tasks:
            task.cancel()
        if self._history_seed_task:
            self._history_seed_task.cancel()
        if self.ws_tasks:
            await asyncio.gather(*self.ws_tasks, return_exceptions=True)
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None

    @staticmethod
    def _chunks(items: list[str], size: int):
        for i in range(0, len(items), size):
            yield items[i:i + size]

    async def _get(self, url: str) -> dict:
        if not self.session:
            raise RuntimeError("PumpScanner is not started")
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if data.get("retCode", 0) != 0:
                raise RuntimeError(data.get("retMsg", "Bybit API error"))
            return data

    async def _refresh_universe(self) -> None:
        cursor = ""
        symbols: list[str] = []
        while True:
            url = BYBIT_INSTRUMENT_URL + (f"&cursor={cursor}" if cursor else "")
            data = await self._get(url)
            result = data.get("result", {})
            for item in result.get("list", []):
                symbol = item.get("symbol", "")
                if item.get("status") == "Trading" and symbol.endswith("USDT"):
                    symbols.append(symbol)
                    self.listings[symbol] = int(item.get("launchTime") or 0)
            cursor = result.get("nextPageCursor") or ""
            if not cursor:
                break
        self.ws_symbols = sorted(set(symbols))
        log.info("Bybit universe loaded: %d trading USDT linear symbols", len(self.ws_symbols))

    async def _load_listings(self) -> None:
        try:
            await self._refresh_universe()
        except Exception:
            log.exception("Could not load complete Bybit instrument universe")

    async def fetch_tickers(self) -> list[dict]:
        data = await self._get(BYBIT_TICKER_URL)
        rows = []
        for x in data.get("result", {}).get("list", []):
            if x.get("symbol", "").endswith("USDT") and float(x.get("lastPrice") or 0) > 0:
                self.ticker_cache[x["symbol"]] = x
                rows.append(x)
        return rows

    async def _ticker_ws_chunk(self, symbols: list[str]) -> None:
        if not symbols:
            return
        topics = [f"tickers.{s}" for s in symbols]
        delay = 2
        while self.running:
            try:
                async with self.session.ws_connect(BYBIT_WS, heartbeat=20, autoping=True) as ws:
                    for batch in self._chunks(topics, 100):
                        await ws.send_json({"op": "subscribe", "args": batch})
                    delay = 2
                    async for msg in ws:
                        if not self.running:
                            return
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        payload = json.loads(msg.data)
                        data = payload.get("data")
                        if not isinstance(data, dict):
                            continue
                        symbol = data.get("symbol")
                        price = float(data.get("lastPrice") or 0)
                        if not symbol or price <= 0:
                            continue
                        old = self.ticker_cache.get(symbol, {})
                        old.update(data)
                        self.ticker_cache[symbol] = old
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("Bybit ticker websocket reconnect: %s", type(exc).__name__)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _seed_history(self) -> None:
        try:
            # REST seed is only for the configured rolling window after a restart.
            # Limit concurrency so Bybit rate limits are respected.
            symbols = list(self.ws_symbols)
            sem = asyncio.Semaphore(8)

            async def seed(symbol: str):
                async with sem:
                    try:
                        minutes = max(10, self.settings.interval_seconds // 60 + 3)
                        limit = min(100, minutes)
                        data = await self._get(BYBIT_KLINE_URL.format(symbol=symbol, interval="1") + f"&limit={limit}")
                        q = self.prices.setdefault(symbol, deque())
                        for row in reversed(data.get("result", {}).get("list", [])):
                            ts, close = float(row[0]) / 1000, float(row[4])
                            if close > 0:
                                q.append((ts, close))
                        self._trim(symbol, time.time())
                    except Exception:
                        pass

            await asyncio.gather(*(seed(s) for s in symbols))
            self.history_ready = bool(self.prices) and len(self.prices) >= max(1, int(len(symbols) * 0.5))
            log.info("Seeded price history for %d/%d symbols; ready=%s", len(self.prices), len(symbols), self.history_ready)
        except asyncio.CancelledError:
            return
        except Exception:
            self.history_ready = False
            log.exception("Could not seed price history")

    def _trim(self, symbol: str, now: float) -> deque[tuple[float, float]]:
        q = self.prices.setdefault(symbol, deque())
        cutoff = now - self.settings.interval_seconds
        while q and q[0][0] < cutoff:
            q.popleft()
        return q

    async def update(self) -> list[PumpSignal]:
        if not self.running:
            return []
        if not self.history_ready:
            now = time.time()
            if now - self.last_diagnostic >= self.diagnostic_interval:
                self.last_diagnostic = now
                log.info("Scanner not ready: universe=%d tickers=%d history=%d", len(self.ws_symbols), len(self.ticker_cache), len(self.prices))
            return []

        # WebSocket is primary. REST refresh is a safety net if a symbol has
        # not arrived yet or the websocket was reconnecting.
        if len(self.ticker_cache) < max(10, int(len(self.ws_symbols) * 0.5)):
            try:
                await self.fetch_tickers()
            except Exception:
                log.warning("Ticker REST recovery failed", exc_info=True)

        now = time.time()
        candidates = []
        for t in list(self.ticker_cache.values()):
            symbol = t.get("symbol", "")
            try:
                price = float(t.get("lastPrice") or 0)
            except (TypeError, ValueError):
                continue
            if not symbol.endswith("USDT") or price <= 0:
                continue

            q = self._trim(symbol, now)
            q.append((now, price))
            if len(q) < 2:
                continue

            low = min(p for _, p in q)
            high = max(p for _, p in q)
            pump_pct = (price - low) / low * 100 if low else 0
            dump_pct = (price - high) / high * 100 if high else 0

            direction = None
            change = 0.0
            start = price
            if pump_pct >= self.settings.threshold_pct:
                direction, change, start = "PUMP", pump_pct, low
            elif abs(dump_pct) >= self.settings.threshold_pct:
                direction, change, start = "DUMP", dump_pct, high
            if not direction:
                continue
            if self.settings.signal_types == "PUMP" and direction != "PUMP":
                continue
            if self.settings.signal_types == "DUMP" and direction != "DUMP":
                continue

            day_pct = float(t.get("price24hPcnt") or 0) * 100
            if self.settings.day_filter_enabled:
                if direction == "PUMP" and day_pct < self.settings.day_min_pct:
                    continue
                if direction == "DUMP" and day_pct > -self.settings.day_min_pct:
                    continue

            key = (symbol, direction)
            if now - self.last_trigger.get(key, 0) < self.settings.cooldown_seconds:
                continue
            candidates.append((t.copy(), direction, change, start, day_pct))

        if not candidates:
            if now - self.last_diagnostic >= self.diagnostic_interval:
                self.last_diagnostic = now
                log.info("Scanner cycle: universe=%d tickers=%d history=%d candidates=0 threshold=%.2f%%", len(self.ws_symbols), len(self.ticker_cache), len(self.prices), self.settings.threshold_pct)
            return []

        log.info("Scanner candidates: %d (universe=%d tickers=%d)", len(candidates), len(self.ws_symbols), len(self.ticker_cache))
        # Do not hit REST for hundreds of candidates at once.
        candidates.sort(key=lambda x: abs(x[2]), reverse=True)
        candidates = candidates[:20]
        results = await asyncio.gather(
            *(self._enrich(*c) for c in candidates),
            return_exceptions=True,
        )
        output = []
        for candidate, result in zip(candidates, results):
            if isinstance(result, Exception) or result is None:
                continue
            self.last_trigger[(result.symbol, result.direction)] = now
            output.append(result)
        log.info("Scanner signals: %d", len(output))
        return output

    async def _enrich(
        self, ticker: dict, direction: str, change: float, start: float, day_pct: float
    ) -> PumpSignal | None:
        symbol = ticker["symbol"]
        rsi = {}
        if self.settings.rsi_enabled:
            values = await asyncio.gather(
                *(self._rsi(symbol, tf) for tf in self.settings.rsi_timeframes),
                return_exceptions=True,
            )
            for tf, value in zip(self.settings.rsi_timeframes, values):
                if isinstance(value, (int, float)):
                    rsi[tf] = float(value)
            if direction == "PUMP" and rsi and not any(v >= self.settings.rsi_overbought for v in rsi.values()):
                return None
            if direction == "DUMP" and rsi and not any(v <= self.settings.rsi_oversold for v in rsi.values()):
                return None
            if self.settings.rsi_enabled and not rsi:
                return None

        imbalance = await self._imbalance(symbol) if self.settings.show_imbalance else None
        volume_spike = await self._volume_spike(symbol) if self.settings.show_volume_spike else None
        oi = self._float_or_none(ticker.get("openInterest"))
        old_oi = self.prev_oi.get(symbol)
        oi_change = ((oi - old_oi) / old_oi * 100) if oi is not None and old_oi else None
        if oi is not None:
            self.prev_oi[symbol] = oi

        score = self._score(direction, change, day_pct, imbalance, volume_spike, rsi, oi_change)
        action, reason, entry_low, entry_high, stop, tp1, tp2 = await self._trade_guidance(
            symbol, direction, float(ticker["lastPrice"]), imbalance
        )

        return PumpSignal(
            symbol=symbol,
            direction=direction,            change_pct=change,
            start_price=start,
            current_price=float(ticker["lastPrice"]),
            day_pct=day_pct,
            imbalance_buy_pct=imbalance,
            volume_24h=float(ticker.get("turnover24h") or 0),
            volume_spike=volume_spike,
            funding_rate=self._float_or_none(ticker.get("fundingRate")),
            open_interest=oi,
            open_interest_change_pct=oi_change,
            listing_ms=self.listings.get(symbol),
            rsi=rsi,
            score=score,
            trade_action=action,
            trade_reason=reason,
            entry_low=entry_low,
            entry_high=entry_high,
            stop_price=stop,
            tp1=tp1,
            tp2=tp2,
            ts=time.time(),
        )

    def _score(self, direction, change, day_pct, imbalance, volume_spike, rsi, oi_change) -> int:
        score = 2
        score += min(3, int(abs(change) / max(self.settings.threshold_pct, 0.1)))
        if volume_spike is not None and volume_spike >= 2:
            score += 2
        if imbalance is not None and (
            (direction == "PUMP" and imbalance >= 52)
            or (direction == "DUMP" and imbalance <= 48)
        ):
            score += 1
        if rsi:
            score += 1
        if oi_change is not None and oi_change > 0:
            score += 1
        if (direction == "PUMP" and day_pct >= 10) or (direction == "DUMP" and day_pct <= -10):
            score += 1
        return min(10, score)

    async def _trade_guidance(self, symbol: str, direction: str, price: float, imbalance: float | None):
        try:
            url = BYBIT_KLINE_URL.format(
                symbol=symbol, interval=self.settings.confirmation_timeframe
            ) + "&limit=30"
            data = await self._get(url)
            rows = list(reversed(data.get("result", {}).get("list", [])))
            closes = [float(r[4]) for r in rows if float(r[4]) > 0]
            n = self.settings.confirmation_candles
            if len(closes) < n + 5:
                return (
                "WAIT",
                "Недостаточно данных для подтверждения. Не угадываем вход.",
                None,
                None,
                None,
                None,
                None,
            )

            recent = closes[-n:]
            move = (recent[-1] / recent[0] - 1) * 100
            if direction == "PUMP":
                aligned = move > 0 and (imbalance is None or imbalance >= 50)
                action = "LONG" if aligned else "WAIT"
                reason = (
                "Цена продолжает двигаться вверх, поэтому сценарий LONG подтверждён."
                if aligned
                else "Цена уже резко выросла, но продолжение вверх пока не подтверждено. Лучше ждать."
            )
            else:
                aligned = move < 0 and (imbalance is None or imbalance <= 50)
                action = "SHORT" if aligned else "WAIT"
                reason = (
                "Цена продолжает двигаться вниз, поэтому сценарий SHORT подтверждён."
                if aligned
                else "Цена уже резко упала, но продолжение вниз пока не подтверждено. Лучше ждать."
            )

            swing_low, swing_high = min(closes[-8:]), max(closes[-8:])
            if action == "LONG":
                stop = swing_low
                risk = max(price - stop, price * 0.002)
                entry_low, entry_high = price * 0.998, price * 1.001
                tp1, tp2 = price + risk * 1.5, price + risk * 2.5
            elif action == "SHORT":
                stop = swing_high
                risk = max(stop - price, price * 0.002)
                entry_low, entry_high = price * 0.999, price * 1.002
                tp1, tp2 = price - risk * 1.5, price - risk * 2.5
            else:
                entry_low = entry_high = stop = tp1 = tp2 = None
            return action, reason, entry_low, entry_high, stop, tp1, tp2
        except Exception:
            log.warning("Trade guidance failed for %s", symbol, exc_info=True)
            return (
                "WAIT",
                "Не удалось получить подтверждение. Сигнал показываем, "
                "но вход не рекомендуем.",
                None,
                None,
                None,
                None,
                None,
            )

    async def _imbalance(self, symbol: str) -> float | None:
        try:
            data = await self._get(BYBIT_ORDERBOOK_URL.format(symbol=symbol))
            bids = data.get("result", {}).get("b", [])
            asks = data.get("result", {}).get("a", [])
            bid = sum(float(x[1]) * float(x[0]) for x in bids)
            ask = sum(float(x[1]) * float(x[0]) for x in asks)
            total = bid + ask
            return bid / total * 100 if total else None
        except Exception:
            return None

    async def _volume_spike(self, symbol: str) -> float | None:
        try:
            data = await self._get(BYBIT_KLINE_URL.format(symbol=symbol, interval="1") + "&limit=21")
            rows = list(reversed(data.get("result", {}).get("list", [])))
            turnovers = [float(r[6]) for r in rows if float(r[6]) > 0]
            if len(turnovers) < 10:
                return None
            current = turnovers[-1]
            baseline = sum(turnovers[:-1]) / len(turnovers[:-1])
            return current / baseline if baseline else None
        except Exception:
            return None

    async def _rsi(self, symbol: str, interval: str) -> float | None:
        try:
            data = await self._get(BYBIT_KLINE_URL.format(symbol=symbol, interval=interval) + "&limit=100")
            closes = [float(r[4]) for r in reversed(data.get("result", {}).get("list", []))]
            if len(closes) < 15:
                return None
            changes = [b - a for a, b in zip(closes[-15:-1], closes[-14:])]
            gains = sum(max(x, 0) for x in changes) / 14
            losses = sum(max(-x, 0) for x in changes) / 14
            if losses == 0:
                return 100.0
            return 100 - 100 / (1 + gains / losses)
        except Exception:
            return None

    async def scan_once(self) -> list[PumpSignal]:
        return await self.update()

    @staticmethod
    def _float_or_none(value):
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def format_signal(self, s: PumpSignal) -> str:
        icon = "🟢" if s.direction == "PUMP" else "🔴"
        name = s.symbol.removesuffix("USDT")
        lines = [
            f"{icon} {name} Bybit #{name.lower()}",
            f"{'Pump' if s.direction == 'PUMP' else 'Dump'}: "
            f"{s.change_pct:+.2f}% ({s.start_price:.8g} → {s.current_price:.8g})",
            "",
            "🧭 Сценарий: "
            f"{'ЛОНГ 🟢' if s.trade_action == 'LONG' else 'ШОРТ 🔴' if s.trade_action == 'SHORT' else 'ЖДАТЬ ⏸️'}",
            f"🧠 Для новичка: {self._beginner_explanation(s)}",
        ]
        if s.trade_action in {"LONG", "SHORT"}:
            lines += [
                f"🎯 Примерная зона входа: {s.entry_low:.8g} – {s.entry_high:.8g}",
                f"🛑 Уровень отмены сценария: {s.stop_price:.8g}",
                f"✅ TP1: {s.tp1:.8g}",
                f"✅ TP2: {s.tp2:.8g}",
                "⚠️ Это расчётная зона по текущим данным, не гарантия и не команда открыть сделку.",
            ]
        else:
            lines.append("⏸️ Вход сейчас не подтверждён. Ждём нового подтверждения, а не угадываем направление.")

        lines.append("")
        if self.settings.show_imbalance and s.imbalance_buy_pct is not None:
            sell = 100 - s.imbalance_buy_pct
            book_icon = "🟢" if s.imbalance_buy_pct >= 50 else "🔴"
            lines.append(f"📉 Дисбаланс стакана: {book_icon} покупки {s.imbalance_buy_pct:.1f}% / продажи {sell:.1f}%")
        if self.settings.show_volume:
            lines.append(f"📈 Объём 24ч: {self._fmt_volume(s.volume_24h)} USDT")
        if self.settings.show_volume_spike and s.volume_spike is not None:
            lines.append(f"⚡ Всплеск объёма: {s.volume_spike:.1f}x к среднему")
        if s.rsi:
            lines.append("📊 RSI: " + " | ".join(f"{tf}м={v:.1f}" for tf, v in s.rsi.items()))
        if self.settings.show_oi and s.open_interest is not None:
            oi_text = f"{s.open_interest:.0f}"
            if s.open_interest_change_pct is not None:
                oi_text += f" ({s.open_interest_change_pct:+.1f}%)"
            lines.append(f"📊 Open Interest: {oi_text}")
        if self.settings.show_funding and s.funding_rate is not None:
            lines.append(f"💰 Funding: {s.funding_rate * 100:.4f}%")
        if self.settings.show_listing and s.listing_ms:
            age_days = max(0, int((time.time() * 1000 - s.listing_ms) / 86_400_000))
            lines.append(f"🗓 Листинг: {age_days} дн.")
        lines.append(f"📈 Изменение за 24ч: {s.day_pct:+.2f}%")
        lines.append(f"📡 Сила события: {s.score}/10")
        lines.append("")
        lines.append(
            "ℹ️ Сигнал означает сильное движение цены. ЛОНГ/ШОРТ здесь — "
            "отдельная проверка продолжения движения; Pump сам по себе не "
            "равен автоматическому ЛОНГУ."
        )
        return "\n".join(lines)

    @staticmethod
    def _beginner_explanation(s: PumpSignal) -> str:
        if s.trade_action == "LONG":
            return (
                "цена резко выросла и последние подтверждающие свечи всё ещё "
                "направлены вверх; ищем вход только в указанной зоне."
            )
        if s.trade_action == "SHORT":
            return (
                "цена резко упала и последние подтверждающие свечи всё ещё "
                "направлены вниз; ищем вход только в указанной зоне."
            )
        return (
            "монета резко двинулась, но продолжение движения не подтверждено; "
            "сейчас входить по одному Pump/Dump рискованно."
        )

    @staticmethod
    def _fmt_volume(value: float) -> str:
        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.1f}B"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K"
        return f"{value:.0f}"