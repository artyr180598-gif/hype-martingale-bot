"""Marshal the existing v1 exchange/data layer into a v3 data bundle.

The v1 ``MarketDataSource`` contract already provides Bybit/Binance/MEXC
failover, REST history, funding, liquidations, order books and demo mode.
v3 does not re-implement exchange connectivity; it validates and normalises
what v1 returns into a ``DataBundle`` consumed by the signal engine.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Any

import pandas as pd

from src.config.settings import Settings
from src.core.logging import get_logger
from src.data.collector import MarketDataSource, build_source
from v3.config import SignalConfig
from v3.models import DataBundle

logger = get_logger("v3.data")

VALID_TFS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}


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


class FuturesDataService:
    """Validating facade over ``MarketDataSource``.

    Responsibilities:
      * fetch OHLCV for multiple timeframes and derivatives/order-flow context;
      * detect stale, duplicate or corrupted data and record ``degraded``;
      * cache short-lived context (BTC, news, global stats);
      * never silently turn an exchange failure into a "fake" live signal --
        demo mode is only used when explicitly configured by MARKET_DATA_MODE.
    """

    def __init__(self, source: MarketDataSource | None = None, cfg: SignalConfig | None = None) -> None:
        self.cfg = cfg or SignalConfig()
        self.settings = Settings()
        self.source = source or build_source(self.settings)[0]
        if source is not None:
            self.settings = getattr(source, "settings", self.settings)
        self._context_cache: dict[str, Any] = {}
        self._context_ts: dict[str, float] = {}
        self._oi_history: dict[str, deque[tuple[float, float]]] = {}
        self._funding_history: dict[str, deque[tuple[float, float]]] = {}

    @property
    def is_demo(self) -> bool:
        return bool(getattr(self.source, "is_demo", False))

    @property
    def mode(self) -> str:
        return getattr(self.source, "mode", getattr(self.source, "name", "unknown"))

    async def probe(self) -> str:
        if hasattr(self.source, "probe"):
            return await self.source.probe()  # type: ignore[no-any-return]
        return self.mode

    async def close(self) -> None:
        await self.source.close()

    # ── Symbol list / tickers ────────────────────────────────────
    async def instruments(self) -> list[Any]:
        return await self.source.discover_instruments()

    async def tickers(self, symbols: list[str] | None = None) -> dict[str, Any]:
        rows = await self.source.get_tickers(symbols)
        return {t.symbol: t for t in rows} if rows else {}

    # ── Klines ───────────────────────────────────────────────────
    async def klines(self, symbol: str, timeframe: str, limit: int | None = None) -> pd.DataFrame:
        if timeframe not in VALID_TFS:
            raise ValueError(f"unsupported timeframe {timeframe}")
        limit = limit or self.cfg.ANALYSIS_BARS
        df = await self.source.get_klines(symbol.upper(), timeframe, limit)
        return _clean_ohlcv(df)

    async def history(self, symbol: str, timeframe: str, bars: int | None = None) -> pd.DataFrame:
        bars = bars or self.cfg.ANALYSIS_BARS
        df = await self.source.get_history(symbol.upper(), timeframe, bars)
        return _clean_ohlcv(df)

    # ── Derivatives / order flow ─────────────────────────────────
    async def funding_history(self, symbol: str, limit: int = 12) -> list[float]:
        try:
            rows = await self.source.get_funding(symbol.upper(), limit)
        except Exception as exc:  # noqa: BLE001
            logger.debug("funding unavailable for %s: %s", symbol, exc)
            return []
        return [float(h.rate) for h in rows if _finite(getattr(h, "rate", None))]

    async def liquidations(self, symbol: str, limit: int = 200) -> list[dict[str, Any]]:
        try:
            rows = await self.source.get_recent_liquidations(limit)
        except Exception as exc:  # noqa: BLE001
            logger.debug("liquidations unavailable for %s: %s", symbol, exc)
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
            if liq.symbol.upper() == symbol.upper()
        ]

    async def orderbook(self, symbol: str, depth: int = 50) -> dict[str, Any] | None:
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

    async def _ticker_map(self) -> dict[str, Any]:
        return await self.tickers()

    async def btc_context(self) -> tuple[float | None, float | None, float | None, float | None, float | None]:
        """(btc_24h_pct, btc_turnover, btc_funding, global_change, btc_dominance)."""
        cache_key = "btc_context"
        cached = self._context_cache.get(cache_key)
        if cached and time.time() - self._context_ts.get(cache_key, 0) < 120:
            return tuple(cached)  # type: ignore[return-value]

        result: tuple[float | None, float | None, float | None, float | None, float | None] = (None, None, None, None, None)
        try:
            tickers = await self.tickers(["BTCUSDT"])
            if tickers:
                t = tickers.get("BTCUSDT")
                if t is not None:
                    result = (
                        float(t.price_24h_pct or 0),
                        float(t.turnover_24h or 0),
                        float(t.funding_rate) if t.funding_rate is not None else None,
                        None,
                        None,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug("btc ticker unavailable: %s", exc)

        try:
            g = await self.source.get_global_stats()
            if g is not None:
                result = (
                    result[0],
                    result[1],
                    result[2],
                    float(g.market_cap_change_24h_pct or 0),
                    float(g.btc_dominance or 0),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("global stats unavailable: %s", exc)

        self._context_cache[cache_key] = result
        self._context_ts[cache_key] = time.time()
        return result

    async def news_sentiment(self, symbol: str) -> float | None:
        cache_key = f"news{self._context_ts.get('news', 0) // 600}"
        if time.time() - self._context_ts.get("news", 0) > 600:
            try:
                items = await self.source.get_news(30)
                self._context_cache["news_items"] = [n.to_dict() for n in items]
                self._context_ts["news"] = time.time()
            except Exception as exc:  # noqa: BLE001
                logger.debug("news unavailable: %s", exc)
                return None
        items = self._context_cache.get("news_items", [])
        base = symbol.upper().replace("USDT", "")
        relevant = [n for n in items if base in [s.upper().replace("USDT", "") for s in n.get("symbols", [])]]
        if not relevant:
            return None
        return float(sum(n.get("sentiment", 0.0) for n in relevant) / len(relevant))

    # ── Bundle builder ───────────────────────────────────────────
    async def build_bundle(self, symbol: str) -> DataBundle:
        symbol = symbol.upper()
        ticker_map = await self.tickers([symbol])
        t = ticker_map.get(symbol)

        btc_pct, btc_turn, btc_fund, global_change, dominance = await self.btc_context()
        funding_hist = await self.funding_history(symbol)
        liqs = await self.liquidations(symbol, 200)
        book = await self.orderbook(symbol, 50)
        news_sent = await self.news_sentiment(symbol)
        try:
            movers = await self.source.get_spot_movers(5)
            price_24h_pct = next((float(m.price_24h_pct) for m in movers if m.symbol.upper() == symbol), None)
        except Exception:  # noqa: BLE001
            price_24h_pct = None

        price = float(t.last) if t is not None else 0.0
        if price <= 0:
            price = 0.0

        historical_price: deque[float] = deque(maxlen=96)
        historical_volume: deque[float] = deque(maxlen=96)
        if t is not None and hasattr(t, "open_24h") and _finite(float(t.open_24h or 0)):
            historical_price.append(float(t.open_24h))
        if t is not None and _finite(float(t.volume_24h or 0)):
            historical_volume.append(float(t.volume_24h))

        degraded: list[str] = []
        data_age_seconds: float | None = None
        if t is None:
            degraded.append("ticker unavailable")
        else:
            if getattr(t, "ts_ms", 0):
                data_age_seconds = max(0.0, (time.time() * 1000 - float(t.ts_ms)) / 1000.0)
            if data_age_seconds is not None and data_age_seconds > self.cfg.MAX_DATA_AGE_SECONDS:
                degraded.append(f"stale ticker ({data_age_seconds:.0f}s old)")
        if not funding_hist:
            degraded.append("funding history unavailable")
        if book is None:
            degraded.append("order book unavailable")
        if global_change is None:
            degraded.append("global context unavailable")

        return DataBundle(
            symbol=symbol,
            price=price,
            price_24h_pct=float(t.price_24h_pct) if t is not None and _finite(t.price_24h_pct) else float(price_24h_pct or 0),
            turnover_24h=float(t.turnover_24h) if t is not None and _finite(t.turnover_24h) else 0.0,
            volume_24h=float(t.volume_24h) if t is not None and _finite(t.volume_24h) else 0.0,
            spread_pct=float(t.spread_pct) if t is not None else None,
            funding_rate=float(t.funding_rate) if t is not None and t.funding_rate is not None else None,
            funding_history=funding_hist,
            open_interest_usd=float(t.open_interest_usd) if t is not None and t.open_interest_usd is not None else None,
            open_interest_history=list(self._oi_history.get(symbol, []))[-24:],
            liquidations=liqs,
            orderbook=book,
            btc_price_24h_pct=btc_pct,
            btc_turnover_24h=btc_turn,
            btc_funding_rate=btc_fund,
            global_change_pct=global_change,
            btc_dominance=dominance,
            news_sentiment=news_sent,
            is_demo=self.is_demo,
            degraded=degraded,
            data_age_seconds=data_age_seconds,
            symbol_price_history=list(historical_price),
            symbol_volume_history=list(historical_volume),
        )

    async def build_scan_bundle(self, symbol: str) -> DataBundle:
        return await self.build_bundle(symbol)
