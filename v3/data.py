"""Marshal the existing v1 exchange/data layer into a v3 data bundle.

The v1 ``MarketDataSource`` contract already provides Bybit/Binance/MEXC
failover, REST history, funding, liquidations, order books and demo mode.
v3 does not re-implement exchange connectivity; it validates and normalises
what v1 returns into a ``DataBundle`` consumed by the signal engine.

This facade adds the production concerns on top of the raw exchange layer:

  * short TTL caches (tickers / klines / order book / funding / liquidations /
    global context) -- public endpoints are fetched often but never blindly;
  * parallel bundle construction (ticker + BTC context + funding +
    liquidations + order book + news are gathered concurrently);
  * derivative history accumulation (open interest series for OI change);
  * a market overview (BTC/ETH/global/Fear&Greed/movers) for "Мой рынок".
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from typing import Any, Awaitable, Callable, TypeVar

import pandas as pd

from src.config.settings import Settings
from src.core.logging import get_logger
from src.data.collector import MarketDataSource, build_source
from v3.analysis.timeframes import build_timeframe_view
from v3.config import SignalConfig
from v3.models import DataBundle

logger = get_logger("v3.data")

VALID_TFS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}
TF_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000,
}

_T = TypeVar("_T")


def _finite(value: float | None) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Drop duplicates, sort, coerce numerics, reject non-finite rows."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    out = df.copy()
    for col in ("ts", "open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["ts", "open", "high", "low", "close", "volume"])
    out = out.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    return out


def _closed_bars(df: pd.DataFrame, timeframe: str, now_ms: int | None = None) -> pd.DataFrame:
    """Remove the still-forming exchange candle from a live REST response.

    Bybit/Binance/MEXC normally include the current candle. Its volume and
    close are not final, so using it makes RVOL, squeeze and breakout tests
    repaint during the bar. Backtest code already passes closed slices; this
    helper is only applied at the data-service boundary.
    """
    if df.empty or timeframe not in TF_MS:
        return df
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    close_at = df["ts"].astype("int64") + TF_MS[timeframe]
    closed = df[close_at <= now]
    return closed.reset_index(drop=True)


class FuturesDataService:
    """Validating facade over ``MarketDataSource``.

    Responsibilities:
      * fetch OHLCV for multiple timeframes and derivatives/order-flow context;
      * detect stale, duplicate or corrupted data and record ``degraded``;
      * cache short-lived context (tickers, klines, order book, BTC, news);
      * own the real Bybit liquidation WebSocket stream (when Bybit is active);
      * never fabricate a value: if a source is unavailable the bundle records
        it in ``degraded`` and the engine answers NO TRADE — синтетических
        данных в продакшн-путях нет вообще.
    """

    def __init__(self, source: MarketDataSource | None = None, cfg: SignalConfig | None = None) -> None:
        self.cfg = cfg or SignalConfig()
        self.settings = Settings()
        self.source = source or build_source(self.settings)[0]
        if source is not None:
            self.settings = getattr(source, "settings", self.settings)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._context_cache: dict[str, Any] = {}
        self._context_ts: dict[str, float] = {}
        self._oi_history: dict[str, deque[tuple[float, float]]] = {}
        self._funding_history: dict[str, deque[tuple[float, float]]] = {}
        self._liq_stream: Any = None

    @property
    def mode(self) -> str:
        return getattr(self.source, "mode", getattr(self.source, "name", "unknown"))

    async def probe(self) -> str:
        mode = self.mode
        if hasattr(self.source, "probe"):
            mode = await self.source.probe()  # type: ignore[no-any-return]
        await self._ensure_liquidation_stream()
        return mode

    # ── реальный WS-поток ликвидаций Bybit ───────────────────────
    async def _ensure_liquidation_stream(self) -> None:
        """Запустить WS-коллектор ликвидаций, если активна Bybit и WS включён."""
        if not self.cfg.LIQUIDATIONS_WS_ENABLED:
            return
        if self.mode != "bybit":
            return
        if self._liq_stream is not None:
            return
        from src.data.liquidations_ws import BybitLiquidationStream

        stream = BybitLiquidationStream(max_age_seconds=self.cfg.LIQUIDATIONS_WS_MAX_AGE_SECONDS)
        symbols = list(self.cfg.watchlist)
        try:
            tickers = await self.tickers(force=True)
            top = sorted(tickers.values(), key=lambda t: float(getattr(t, "turnover_24h", 0) or 0), reverse=True)
            symbols += [t.symbol for t in top[:40]]
        except Exception:  # noqa: BLE001
            pass
        try:
            await stream.start(symbols)
            self._liq_stream = stream
            logger.info("WS-поток ликвидаций Bybit запущен (%d символов)", len(stream.symbols))
        except Exception as exc:  # noqa: BLE001
            logger.warning("WS-поток ликвидаций не запустился: %s (ликвидации будут «н/д»)", exc)

    @property
    def liquidation_stream(self) -> Any:
        return self._liq_stream

    def source_diagnostics(self) -> list[dict[str, Any]]:
        """Диагностика по каждому реальному источнику (для pulse/status/health)."""
        diag_fn = getattr(self.source, "diagnostics", None)
        rows: list[dict[str, Any]] = diag_fn() if callable(diag_fn) else []
        if self._liq_stream is not None:
            rows.append({"source": "bybit-liquidations-ws", **self._liq_stream.diagnostics()})
        return rows

    async def close(self) -> None:
        if self._liq_stream is not None:
            try:
                await self._liq_stream.stop()
            except Exception:  # noqa: BLE001
                pass
            self._liq_stream = None
        await self.source.close()

    # ── TTL cache helper ─────────────────────────────────────────
    async def _cached(self, key: str, ttl: float, fetch: Callable[[], Awaitable[_T]]) -> _T:
        now = time.time()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]  # type: ignore[return-value]
        value = await fetch()
        self._cache[key] = (now, value)
        return value

    def _invalidate(self, key_prefix: str) -> None:
        for key in list(self._cache):
            if key.startswith(key_prefix):
                self._cache.pop(key, None)

    # ── Symbol list / tickers ────────────────────────────────────
    async def instruments(self) -> list[Any]:
        return await self.source.discover_instruments()

    async def tickers(self, symbols: list[str] | None = None, force: bool = False) -> dict[str, Any]:
        if force:
            self._invalidate("tickers:")
        key = "tickers:*" if not symbols else "tickers:" + ",".join(sorted(s.upper() for s in symbols))
        rows = await self._cached(
            key,
            self.cfg.TICKER_CACHE_TTL_SECONDS,
            lambda: self.source.get_tickers(symbols),
        )
        return {t.symbol: t for t in rows} if rows else {}

    # ── Klines ───────────────────────────────────────────────────
    async def klines(self, symbol: str, timeframe: str, limit: int | None = None) -> pd.DataFrame:
        if timeframe not in VALID_TFS:
            raise ValueError(f"unsupported timeframe {timeframe}")
        limit = limit or self.cfg.ANALYSIS_BARS
        key = f"klines:{symbol.upper()}:{timeframe}:{limit}"

        async def _fetch() -> pd.DataFrame:
            df = await self.source.get_klines(symbol.upper(), timeframe, limit)
            return _closed_bars(_clean_ohlcv(df), timeframe)

        return await self._cached(key, self.cfg.KLINES_CACHE_TTL_SECONDS, _fetch)

    async def history(self, symbol: str, timeframe: str, bars: int | None = None) -> pd.DataFrame:
        bars = bars or self.cfg.ANALYSIS_BARS
        key = f"history:{symbol.upper()}:{timeframe}:{bars}"

        async def _fetch() -> pd.DataFrame:
            df = await self.source.get_history(symbol.upper(), timeframe, bars)
            return _closed_bars(_clean_ohlcv(df), timeframe)

        return await self._cached(key, self.cfg.KLINES_CACHE_TTL_SECONDS, _fetch)

    # ── Derivatives / order flow ─────────────────────────────────
    async def funding_history(self, symbol: str, limit: int = 12) -> list[float]:
        key = f"funding:{symbol.upper()}:{limit}"

        async def _fetch() -> list[float]:
            try:
                rows = await self.source.get_funding(symbol.upper(), limit)
            except Exception as exc:  # noqa: BLE001
                logger.debug("funding unavailable for %s: %s", symbol, exc)
                return []
            return [float(h.rate) for h in rows if _finite(getattr(h, "rate", None))]

        return await self._cached(key, self.cfg.FUNDING_CACHE_TTL_SECONDS, _fetch)

    async def liquidations(self, symbol: str, limit: int = 200) -> list[dict[str, Any]]:
        """Реальные ликвидации ``symbol`` с биржевыми timestamp.

        Bybit — живой публичный WS-поток (``src/data/liquidations_ws.py``);
        Binance — публичный REST ``allForceOrders``. Прокси «крупных сделок»
        за ликвидации НЕ выдаётся: поток недоступен → пусто, а аналитика
        показывает «н/д» (без влияния на скоринг).
        """
        # 1) живой WS-поток Bybit — без HTTP-запросов вовсе
        if self._liq_stream is not None and self.mode == "bybit":
            rows = self._liq_stream.events(symbol, self.cfg.LIQUIDATIONS_WS_MAX_AGE_SECONDS)
            return [
                {
                    "symbol": liq.symbol,
                    "side": str(liq.side),
                    "size": float(liq.size or 0),
                    "price": float(liq.price or 0),
                    "ts_ms": int(liq.ts_ms or 0),
                }
                for liq in rows[:limit]
            ]

        # 2) остальные биржи — какой РЕАЛЬНЫЙ фид умеет источник (Binance REST)
        key = "liquidations:*"

        async def _fetch() -> list[dict[str, Any]]:
            try:
                rows = await self.source.get_recent_liquidations(limit)
            except Exception as exc:  # noqa: BLE001
                logger.debug("liquidations unavailable: %s", exc)
                return []
            return [
                {
                    "symbol": liq.symbol,
                    "side": str(liq.side),
                    "size": float(liq.size or 0),
                    "price": float(liq.price or 0),
                    "ts_ms": int(liq.ts_ms or 0),
                }
                for liq in rows
            ]

        rows = await self._cached(key, self.cfg.LIQUIDATIONS_CACHE_TTL_SECONDS, _fetch)
        return [r for r in rows if r["symbol"].upper() == symbol.upper()]

    async def account_ratio(self, symbol: str) -> float | None:
        """Bybit long-account ratio (0..1); other exchanges return None."""
        key = f"lsr:{symbol.upper()}"

        async def _fetch() -> float | None:
            try:
                value = await self.source.get_account_ratio(symbol.upper())
            except Exception as exc:  # noqa: BLE001
                logger.debug("account ratio unavailable for %s: %s", symbol, exc)
                return None
            return float(value) if value is not None and _finite(value) else None

        return await self._cached(key, self.cfg.FUNDING_CACHE_TTL_SECONDS, _fetch)

    async def orderbook(self, symbol: str, depth: int | None = None) -> dict[str, Any] | None:
        depth = depth or self.cfg.ORDERBOOK_DEPTH
        key = f"book:{symbol.upper()}:{depth}"

        async def _fetch() -> dict[str, Any] | None:
            try:
                book = await self.source.get_orderbook(symbol.upper(), depth)
            except Exception as exc:  # noqa: BLE001
                logger.debug("orderbook unavailable for %s: %s", symbol, exc)
                return None
            if book is None:
                return None
            return {
                "bids": [(float(p), float(q)) for p, q in (book.bids or [])],
                "asks": [(float(p), float(q)) for p, q in (book.asks or [])],
                "ts_ms": int(book.ts_ms or 0),
            }

        return await self._cached(key, self.cfg.ORDERBOOK_CACHE_TTL_SECONDS, _fetch)

    async def _ticker_map(self) -> dict[str, Any]:
        return await self.tickers()

    # ── global / news / spot context ─────────────────────────────
    async def btc_context(self) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None, float | None]:
        """(btc_24h_pct, btc_turnover, btc_funding, global_change, btc_dominance,
        eth_24h_pct, eth_funding)."""
        cache_key = "btc_context"
        cached = self._context_cache.get(cache_key)
        if cached and time.time() - self._context_ts.get(cache_key, 0) < 120:
            return tuple(cached)  # type: ignore[return-value]

        result: tuple[float | None, float | None, float | None, float | None, float | None, float | None, float | None] = (
            None, None, None, None, None, None, None,
        )
        try:
            tickers = await self.tickers(["BTCUSDT", "ETHUSDT"])
            btc = tickers.get("BTCUSDT")
            eth = tickers.get("ETHUSDT")
            if btc is not None:
                result = (
                    float(btc.price_24h_pct or 0),
                    float(btc.turnover_24h or 0),
                    float(btc.funding_rate) if btc.funding_rate is not None else None,
                    None,
                    None,
                    float(eth.price_24h_pct) if eth is not None and _finite(eth.price_24h_pct) else None,
                    float(eth.funding_rate) if eth is not None and eth.funding_rate is not None else None,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("btc/eth ticker unavailable: %s", exc)

        try:
            g = await self.source.get_global_stats()
            if g is not None:
                result = (
                    result[0],
                    result[1],
                    result[2],
                    float(g.market_cap_change_24h_pct or 0),
                    float(g.btc_dominance or 0),
                    result[5],
                    result[6],
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("global stats unavailable: %s", exc)

        self._context_cache[cache_key] = result
        self._context_ts[cache_key] = time.time()
        return result

    @staticmethod
    def _relevant_news(items: list[Any], symbol: str, limit: int = 6) -> list[dict[str, Any]]:
        base = symbol.upper().replace("USDT", "")
        out: list[dict[str, Any]] = []
        for n in items:
            syms = [s.upper().replace("USDT", "") for s in n.get("symbols", [])]
            relevant = base in syms or base in n.get("title", "").upper()
            if n.get("symbols") and not relevant:
                continue
            out.append({
                "title": str(n.get("title", ""))[:160],
                "source": str(n.get("source", "unknown")),
                "ts_ms": int(n.get("ts_ms", 0) or 0),
                "url": str(n.get("url", "")),
                "sentiment": float(n.get("sentiment", 0.0) or 0.0),
                "relevant": bool(relevant),
            })
        out.sort(key=lambda x: (abs(x["sentiment"]), x["ts_ms"]), reverse=True)
        return out[:limit]

    async def _news_items(self, limit: int = 60) -> list[dict[str, Any]]:
        if time.time() - self._context_ts.get("news", 0) > 600:
            try:
                items = await self.source.get_news(limit)
                self._context_cache["news_items"] = [n.to_dict() for n in items]
                self._context_ts["news"] = time.time()
            except Exception as exc:  # noqa: BLE001
                logger.debug("news unavailable: %s", exc)
                return list(self._context_cache.get("news_items", []))
        return list(self._context_cache.get("news_items", []))

    async def news_sentiment(self, symbol: str) -> float | None:
        items = await self._news_items(60)
        relevant = self._relevant_news(items, symbol, limit=6)
        if not relevant:
            return None
        return round(sum(float(n["sentiment"]) for n in relevant) / len(relevant), 3)

    async def news_items(self, symbol: str, limit: int = 3) -> list[dict[str, Any]]:
        items = await self._news_items(60)
        return self._relevant_news(items, symbol, limit=limit)

    async def _record_derivatives_history(self, symbol: str, ts_ms: int, oi: float | None, funding: float | None) -> None:
        """Accumulate per-symbol OI/funding series so OI change can be measured."""
        if oi is not None and _finite(oi) and oi > 0:
            dq = self._oi_history.setdefault(symbol, deque(maxlen=24))
            if not dq or dq[-1] != (ts_ms, float(oi)):
                dq.append((ts_ms, float(oi)))
        if funding is not None and _finite(funding):
            dq = self._funding_history.setdefault(symbol, deque(maxlen=24))
            if not dq or dq[-1] != (ts_ms, float(funding)):
                dq.append((ts_ms, float(funding)))

    @staticmethod
    def _oi_change_pct(history: deque[tuple[float, float]]) -> float | None:
        if len(history) < 2:
            return None
        first, last = history[0][1], history[-1][1]
        if first <= 0:
            return None
        return round((last / first - 1.0) * 100.0, 3)

    # ── Bundle builder ───────────────────────────────────────────
    MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK"}

    async def build_bundle(self, symbol: str, deep: bool = True) -> DataBundle:
        """Build one ``DataBundle`` for ``symbol``.

        ``deep=False`` (Stage-1 / light scan mode) skips the news/sentiment and
        CoinGecko-mover calls and uses a shallower order book -- a deliberate
        trade-off: scanning many symbols must not trigger one HTTP request per
        data source per symbol.
        """
        symbol = symbol.upper()

        async def _safe(awaitable: Awaitable[_T], default: _T) -> _T:
            try:
                return await awaitable
            except Exception as exc:  # noqa: BLE001
                logger.debug("bundle part unavailable for %s: %s", symbol, exc)
                return default

        depth = self.cfg.ORDERBOOK_DEPTH if deep else 25
        tasks: dict[str, Awaitable[Any]] = {
            "tickers": _safe(self.tickers([symbol]), {}),
            "btc": _safe(self.btc_context(), (None,) * 7),
            "funding": _safe(self.funding_history(symbol), []),
            "liqs": _safe(self.liquidations(symbol, 200), []),
            "book": _safe(self.orderbook(symbol, depth), None),
        }
        if deep:
            tasks["news"] = _safe(self.news_items(symbol, 3), [])
            tasks["movers"] = _safe(self.source.get_spot_movers(5), [])
            tasks["lsr"] = _safe(self.account_ratio(symbol), None)
        else:
            # Stage-1 light scan: skip news / spot movers / account ratio
            # entirely so 20+ symbols cost ~0 extra HTTP round-trips.
            tasks["news"] = asyncio.sleep(0, result=[])
            tasks["movers"] = asyncio.sleep(0, result=[])
            tasks["lsr"] = asyncio.sleep(0, result=None)
        results = dict(zip(tasks, await asyncio.gather(*tasks.values())))
        ticker_map = results["tickers"]
        (btc_pct, btc_turn, btc_fund, global_change, dominance, eth_pct, eth_fund) = results["btc"]
        funding_hist = results["funding"]
        liqs = results["liqs"]
        book = results["book"]
        news_items = results["news"]
        movers = results["movers"]
        account_ratio = results["lsr"]
        news_sent = (
            round(sum(float(n["sentiment"]) for n in news_items[:3]) / len(news_items[:3]), 3)
            if news_items
            else None
        )

        t = ticker_map.get(symbol)
        price = float(t.last) if t is not None else 0.0
        if price < 0 or not _finite(price):
            price = 0.0

        ts_ms = time.time() * 1000
        oi = float(t.open_interest_usd) if t is not None and _finite(getattr(t, "open_interest_usd", None)) else None
        funding = float(t.funding_rate) if t is not None and t.funding_rate is not None else None
        await self._record_derivatives_history(symbol, int(ts_ms), oi, funding)
        oi_history = list(self._oi_history.get(symbol, []))[-24:]
        oi_change = self._oi_change_pct(self._oi_history.get(symbol, deque()))

        # 24h price as fallback from CoinGecko spot movers (spot, not perp)
        price_24h_pct = float(t.price_24h_pct) if t is not None and _finite(t.price_24h_pct) else None
        if price_24h_pct is None:
            price_24h_pct = next(
                (float(m.price_24h_pct) for m in movers if m.symbol.upper() == symbol),
                None,
            )
        price_24h_pct = price_24h_pct or 0.0

        degraded: list[str] = []
        data_age_seconds: float | None = None
        if t is None:
            degraded.append("ticker unavailable (нет реальных данных биржи)")
        elif _finite(getattr(t, "ts_ms", None)):
            data_age_seconds = max(0.0, (ts_ms - float(t.ts_ms)) / 1000.0)
            if data_age_seconds > self.cfg.MAX_DATA_AGE_SECONDS:
                degraded.append(f"stale ticker ({data_age_seconds:.0f}s old)")
        if not funding_hist and funding is None:
            degraded.append("funding history unavailable")
        if book is None:
            degraded.append("order book unavailable")
        if global_change is None:
            degraded.append("global context unavailable")
        if self.cfg.LIQUIDATIONS_WS_ENABLED and self.mode == "bybit":
            # ликвидации либо реальные (WS), либо честное «н/д» — без прокси
            if self._liq_stream is None or not self._liq_stream.healthy:
                degraded.append("liquidations unavailable — показатель «н/д»")

        return DataBundle(
            symbol=symbol,
            ts_ms=int(ts_ms),
            price=price,
            price_24h_pct=float(price_24h_pct),
            turnover_24h=float(t.turnover_24h) if t is not None and _finite(t.turnover_24h) else 0.0,
            volume_24h=float(t.volume_24h) if t is not None and _finite(t.volume_24h) else 0.0,
            spread_pct=float(t.spread_pct) if t is not None else None,
            funding_rate=funding,
            funding_history=funding_hist,
            open_interest_usd=oi,
            open_interest_history=[(ts, v) for ts, v in oi_history],
            oi_change_24h_pct=oi_change,
            long_short_ratio=float(account_ratio) if account_ratio is not None else None,
            mark_price=float(t.mark_price) if t is not None and _finite(getattr(t, "mark_price", None)) else None,
            index_price=float(t.index_price) if t is not None and _finite(getattr(t, "index_price", None)) else None,
            liquidations=liqs,
            orderbook=book,
            btc_price_24h_pct=btc_pct,
            btc_turnover_24h=btc_turn,
            btc_funding_rate=btc_fund,
            eth_price_24h_pct=eth_pct,
            eth_funding_rate=eth_fund,
            global_change_pct=global_change,
            btc_dominance=dominance,
            news_sentiment=news_sent,
            news_items=news_items[:3],
            degraded=degraded,
            data_age_seconds=data_age_seconds,
            symbol_price_history=[float(t.open_24h)] if t is not None and _finite(getattr(t, "open_24h", None)) else [],
            symbol_volume_history=[float(t.volume_24h)] if t is not None and _finite(t.volume_24h) else [],
        )

    async def build_scan_bundle(self, symbol: str) -> DataBundle:
        return await self.build_bundle(symbol)

    # ── Market overview ("Мой рынок") ────────────────────────────
    async def market_overview(self, force: bool = False) -> dict[str, Any]:
        """Compact market-wide snapshot for the Telegram ``Мой рынок`` screen."""
        started = time.time()
        tickers = await self.tickers(force=force)
        btc_pct, _, btc_fund, global_change, dominance, eth_pct, eth_fund = await self.btc_context()
        btc = tickers.get("BTCUSDT")
        eth = tickers.get("ETHUSDT")

        btc_trend = "flat"
        btc_atr_pct: float | None = None
        try:
            df = await self.klines("BTCUSDT", "1h", self.cfg.ANALYSIS_BARS)
            if len(df) >= 40:
                view = build_timeframe_view(df, "1h")
                btc_trend = view.trend
                btc_atr_pct = view.atr_pct
        except Exception as exc:  # noqa: BLE001
            logger.debug("BTC 1h overview unavailable: %s", exc)

        rows = [t for t in tickers.values() if _finite(getattr(t, "turnover_24h", None)) and float(t.turnover_24h or 0) > 0]
        rows.sort(key=lambda t: float(t.turnover_24h or 0), reverse=True)

        def _small(t: Any) -> dict[str, Any]:
            return {
                "symbol": t.symbol,
                "price": float(t.last or 0),
                "price_24h_pct": float(t.price_24h_pct or 0),
                "turnover_24h": float(t.turnover_24h or 0),
                "funding_rate": t.funding_rate,
                "open_interest_usd": t.open_interest_usd,
            }

        gainers = sorted((t for t in rows if float(t.price_24h_pct or 0) > 0), key=lambda t: float(t.price_24h_pct), reverse=True)[:10]
        losers = sorted((t for t in rows if float(t.price_24h_pct or 0) < 0), key=lambda t: float(t.price_24h_pct))[:10]

        try:
            g = await self.source.get_global_stats()
            fear_greed = g.fear_greed.to_dict() if g and g.fear_greed else None
            market_cap = float(g.total_market_cap_usd or 0) if g else 0.0
            total_volume = float(g.total_volume_24h_usd or 0) if g else 0.0
        except Exception as exc:  # noqa: BLE001
            logger.debug("global stats unavailable in overview: %s", exc)
            fear_greed, market_cap, total_volume = None, 0.0, 0.0

        return {
            "ts_ms": int(time.time() * 1000),
            "mode": self.mode,
            "btc": _small(btc) if btc else None,
            "eth": _small(eth) if eth else None,
            "btc_trend": btc_trend,
            "btc_atr_pct": round(btc_atr_pct, 3) if btc_atr_pct is not None else None,
            "eth_24h_pct": eth_pct,
            "btc_funding_rate": btc_fund,
            "eth_funding_rate": eth_fund,
            "global": {
                "market_cap_change_24h_pct": global_change,
                "btc_dominance": dominance,
                "total_market_cap_usd": market_cap,
                "total_volume_24h_usd": total_volume,
                "fear_greed": fear_greed,
            },
            "universe_count": len(rows),
            "total_turnover_24h": round(sum(float(t.turnover_24h or 0) for t in rows), 2),
            "avg_move_24h_pct": round(sum(float(t.price_24h_pct or 0) for t in rows) / len(rows), 3) if rows else 0.0,
            "top_turnover": [_small(t) for t in rows[:10]],
            "gainers": [_small(t) for t in gainers],
            "losers": [_small(t) for t in losers],
            "duration_sec": round(time.time() - started, 2),
        }
