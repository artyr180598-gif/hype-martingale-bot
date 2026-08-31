"""
Слой рыночных данных: единый интерфейс + коннекторы бирж.

Архитектура по образцу ccxt / Hummingbot:
  MarketDataSource (контракт)
    ├── BybitSource     — основной источник (фьючерсы USDT-perp, V5 public API)
    ├── BinanceSource   — резерв (USDⓈ-M фьючерсы)
    ├── MexcSource      — третий источник
    └── DemoMarketSource— синтетический рынок (тесты / нет доступа к биржам)

  EnrichedSource оборачивает биржевой источник и добавляет спот-контекст:
  CoinGecko (муверы, тренды, глобальная статистика), Fear & Greed, новости.

Публичные эндпоинты работают БЕЗ ключей. Ключи (BYBIT_API_KEY/SECRET и т.п.)
нужны только для приватных данных; советник обходится публичными.
"""

from __future__ import annotations

import abc
import asyncio
import time
from typing import Any

import httpx
import pandas as pd

from src.config.settings import Settings
from src.core.errors import DataSourceError, RateLimitError, UnknownSymbol
from src.core.logging import get_logger
from src.core.timeutil import now_ms, tf_ms
from src.data.models import (
    CoinMover,
    FearGreed,
    FundingEntry,
    GlobalStats,
    Instrument,
    Liquidation,
    NewsItem,
    OrderBook,
    Ticker,
    normalize_symbol,
)

logger = get_logger("data.collector")

QUOTE = "USDT"


# ════════════════════════════════════════════════════════════════
#  КОНТРАКТ
# ════════════════════════════════════════════════════════════════
class MarketDataSource(abc.ABC):
    """Интерфейс источника рыночных данных."""

    name: str = "base"
    is_demo: bool = False

    @abc.abstractmethod
    async def discover_instruments(self, category: str = "linear") -> list[Instrument]: ...

    @abc.abstractmethod
    async def get_klines(self, symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame: ...

    async def get_history(
        self, symbol: str, timeframe: str = "1h", bars: int = 1000, max_requests: int = 40
    ) -> pd.DataFrame:
        """
        Глубокая история для бэктеста: столько баров, сколько попросили.

        Базовая реализация делает один запрос — этого достаточно для
        источников без курсора. Bybit/Binance переопределяют метод
        пагинацией по end/endTime, потому что один ответ биржи
        ограничен 1000/1500 свечами.
        """
        return await self.get_klines(symbol, timeframe, bars)

    @abc.abstractmethod
    async def get_tickers(self, symbols: list[str] | None = None) -> list[Ticker]: ...

    @abc.abstractmethod
    async def get_funding(self, symbol: str, limit: int = 12) -> list[FundingEntry]: ...

    @abc.abstractmethod
    async def get_recent_liquidations(self, limit: int = 200) -> list[Liquidation]: ...

    @abc.abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 25) -> OrderBook: ...

    @abc.abstractmethod
    async def get_spot_movers(self, limit: int = 25) -> list[CoinMover]: ...

    @abc.abstractmethod
    async def get_trending(self, limit: int = 12) -> list[CoinMover]: ...

    @abc.abstractmethod
    async def get_fear_greed(self) -> FearGreed: ...

    @abc.abstractmethod
    async def get_global_stats(self) -> GlobalStats: ...

    @abc.abstractmethod
    async def get_news(self, limit: int = 20) -> list[NewsItem]: ...

    async def get_account_ratio(self, symbol: str) -> float | None:
        """Количество длинных позиций по символу (0..1) — только Bybit,
        остальные источники не отдают этот публичный эндпоинт."""
        return None

    def get_instrument(self, symbol: str) -> Instrument | None:
        return None

    async def close(self) -> None:
        pass

    @staticmethod
    def _df(rows: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(rows)
        for col in ("ts", "open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "ts" in df.columns:
            df = df.sort_values("ts").reset_index(drop=True)
        return df


# ════════════════════════════════════════════════════════════════
#  HTTP-БАЗА: ретраи, backoff, таймауты (как в ccxt)
# ════════════════════════════════════════════════════════════════
class _Http:
    def __init__(self, settings: Settings, base_url: str, headers: dict | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.base_url = base_url.rstrip("/")
        hdrs = {"User-Agent": "hype-advisor/5.0"}
        hdrs.update({k: v for k, v in (headers or {}).items() if v})
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=settings.HTTP_TIMEOUT_SECONDS,
            headers=hdrs,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._sem = asyncio.Semaphore(8)

    async def get(self, path: str, params: dict | None = None, retries: int | None = None) -> Any:
        """GET with retry + exponential backoff, including HTTP 429.

        429 (rate limit) is transient: we back off (honouring Retry-After when
        the exchange provides it) and retry instead of failing immediately.
        Other 4xx are treated as permanent and raised right away.
        """
        retries = self.settings.HTTP_MAX_RETRIES if retries is None else retries
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                async with self._sem:
                    r = await self._client.get(f"{self.base_url}{path}", params=params or {})
                if r.status_code == 429:
                    retry_after = _retry_after_seconds(r.headers)
                    await asyncio.sleep(max(retry_after, 0.4 * (2**attempt)))
                    raise RateLimitError(f"{path}: 429 rate limit")
                if r.status_code == 404:
                    raise UnknownSymbol(f"{path}: 404 {r.text[:120]}")
                if r.status_code >= 500:
                    raise DataSourceError(f"{path}: HTTP {r.status_code} {r.text[:120]}")
                if r.status_code >= 400:
                    raise DataSourceError(f"{path}: HTTP {r.status_code} {r.text[:120]}")
                return r.json()
            except RateLimitError as e:
                last_err = e
                if attempt >= retries - 1:
                    raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < retries - 1:
                    await asyncio.sleep(0.4 * (2**attempt))
        raise DataSourceError(f"{getattr(self, 'name', 'http')} {path}: {last_err}")

    async def close(self) -> None:
        await self._client.aclose()


def _retry_after_seconds(headers: Any) -> float:
    try:
        raw = (headers or {}).get("retry-after")
        if raw is None:
            return 0.0
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _tf_to_bybit(timeframe: str) -> str:
    return {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60",
            "2h": "120", "4h": "240", "6h": "360", "12h": "720", "1d": "D"}.get(timeframe, "15")


def _tf_to_mexc(timeframe: str) -> str:
    return {"1m": "Min1", "5m": "Min5", "15m": "Min15", "30m": "Min30", "1h": "Min60",
            "4h": "Hour4", "8h": "Hour8", "1d": "Day1", "1w": "Week1"}.get(timeframe, "Min15")


# ════════════════════════════════════════════════════════════════
#  BYBIT V5 (основной источник)
# ════════════════════════════════════════════════════════════════
class BybitSource(_Http, MarketDataSource):
    name = "bybit"
    is_demo = False

    def __init__(self, settings: Settings):
        host = "https://api-testnet.bybit.com" if settings.BYBIT_TESTNET else "https://api.bybit.com"
        super().__init__(settings, host)
        self._instruments: list[Instrument] = []
        self._lsr_cache: dict[str, tuple[float, float | None]] = {}

    @staticmethod
    def _unwrap(payload: dict, path: str) -> Any:
        code = payload.get("retCode", 0)
        if code != 0:
            raise DataSourceError(f"bybit {path}: retCode={code} {payload.get('retMsg')}")
        return payload.get("result")

    async def discover_instruments(self, category: str = "linear") -> list[Instrument]:
        if self._instruments:
            return self._instruments
        raw = await self.get("/v5/market/instruments-info", {"category": category, "limit": 1000})
        res = self._unwrap(raw, "instruments-info") or {}
        out: list[Instrument] = []
        for it in res.get("list", []):
            sym = it.get("symbol", "")
            if not sym.endswith(QUOTE):
                continue
            out.append(
                Instrument(
                    symbol=sym,
                    base=str(it.get("baseCoin", sym.replace(QUOTE, ""))),
                    quote=str(it.get("quoteCoin", QUOTE)),
                    category=category,
                    status=str(it.get("status", "Trading")),
                    price_scale=int(it.get("priceScale", 4) or 4),
                    qty_scale=int(str(it.get("lotSizeFilter", {}).get("qtyStep", "0.001")).split(".")[-1].rstrip("0") or 3),
                    tick_size=float(it.get("priceFilter", {}).get("tickSize", 0.0001) or 0.0001),
                    qty_step=float(it.get("lotSizeFilter", {}).get("qtyStep", 0.001) or 0.001),
                    min_qty=float(it.get("lotSizeFilter", {}).get("minOrderQty", 0) or 0),
                    min_notional=float(it.get("lotSizeFilter", {}).get("minOrderAmt", 5) or 5),
                    max_leverage=int(it.get("leverageFilter", {}).get("maxLeverage", 50) or 50),
                    maker_fee=0.0002,
                    taker_fee=0.00055,
                )
            )
        self._instruments = out
        return out

    def get_instrument(self, symbol: str) -> Instrument | None:
        symbol = symbol.upper()
        for i in self._instruments:
            if i.symbol == symbol:
                return i
        return None

    async def get_klines(self, symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
        symbol = symbol.upper()
        raw = await self.get(
            "/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": _tf_to_bybit(timeframe), "limit": min(limit, 1000)},
        )
        res = self._unwrap(raw, "kline") or {}
        rows = res.get("list", [])
        if not rows:
            raise UnknownSymbol(f"{symbol}: нет свечей на Bybit")
        data = [
            {
                "ts": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
        return self._df(data).tail(limit).reset_index(drop=True)

    async def get_history(
        self, symbol: str, timeframe: str = "1h", bars: int = 1000, max_requests: int = 40
    ) -> pd.DataFrame:
        symbol = symbol.upper()
        rows: list[list] = []
        end: int | None = None
        for _ in range(max(1, max_requests)):
            params = {
                "category": "linear", "symbol": symbol,
                "interval": _tf_to_bybit(timeframe), "limit": 1000,
            }
            if end is not None:
                params["end"] = end
            raw = await self.get("/v5/market/kline", params)
            res = self._unwrap(raw, "kline") or {}
            lst = res.get("list", []) or []
            if not lst:
                break
            rows.extend(lst)
            if len(rows) >= bars or len(lst) < 2:
                break
            end = min(int(r[0]) for r in lst) - 1
        if not rows:
            raise UnknownSymbol(f"{symbol}: нет истории на Bybit")
        data = [
            {"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in rows
        ]
        df = self._df(data)
        df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        return df.tail(bars).reset_index(drop=True)

    async def get_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        raw = await self.get("/v5/market/tickers", {"category": "linear"})
        res = self._unwrap(raw, "tickers") or {}
        out: list[Ticker] = []
        for t in res.get("list", []):
            sym = t.get("symbol", "")
            if symbols and sym.upper() not in [s.upper() for s in symbols]:
                continue
            if not sym.endswith(QUOTE):
                continue
            last = float(t.get("lastPrice") or 0)
            if last <= 0:
                continue
            out.append(
                Ticker(
                    symbol=sym,
                    last=last,
                    price_24h_pct=float(t.get("price24hPcnt") or 0) * 100.0,
                    turnover_24h=float(t.get("turnover24h") or 0),
                    volume_24h=float(t.get("volume24h") or 0),
                    high_24h=float(t.get("highPrice24h") or 0),
                    low_24h=float(t.get("lowPrice24h") or 0),
                    open_24h=float(t.get("prevPrice24h") or 0),
                    bid=float(t.get("bid1Price") or 0),
                    ask=float(t.get("ask1Price") or 0),
                    funding_rate=_none_or_float(t.get("fundingRate")),
                    next_funding_ms=_none_or_int(t.get("nextFundingTime")),
                    open_interest=_none_or_float(t.get("openInterest")),
                    open_interest_usd=_none_or_float(t.get("openInterestValue")),
                    mark_price=_none_or_float(t.get("markPrice")),
                    index_price=_none_or_float(t.get("indexPrice")),
                    ts_ms=now_ms(),
                )
            )
        return out

    async def get_account_ratio(self, symbol: str) -> float | None:
        """Публичный ratio «длинные счета / все счета» (0..1) по символу."""
        symbol = symbol.upper()
        cached = self._lsr_cache.get(symbol)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        try:
            raw = await self.get(
                "/v5/market/account-ratio",
                {"category": "linear", "symbol": symbol, "period": "1h", "limit": 1},
            )
            res = self._unwrap(raw, "account-ratio") or {}
            rows = res.get("list", []) or []
            value = _none_or_float(rows[0].get("buyRatio")) if rows else None
        except DataSourceError:
            # публичный эндпоинт может быть недоступен в регионе/тестнете —
            # это контекст, а не причина отказывать в анализе
            value = None
        self._lsr_cache[symbol] = (time.time(), value)
        return value

    async def get_funding(self, symbol: str, limit: int = 12) -> list[FundingEntry]:
        raw = await self.get(
            "/v5/market/funding/history",
            {"category": "linear", "symbol": symbol.upper(), "limit": min(limit, 200)},
        )
        res = self._unwrap(raw, "funding/history") or {}
        rows = res.get("list", [])
        rows = sorted(rows, key=lambda r: int(r.get("fundingRateTimestamp") or 0))
        return [
            FundingEntry(
                ts_ms=int(r.get("fundingRateTimestamp") or 0),
                rate=float(r.get("fundingRate") or 0),
                symbol=symbol.upper(),
            )
            for r in rows[-limit:]
        ]

    async def get_recent_liquidations(self, limit: int = 200) -> list[Liquidation]:
        """
        Публичного REST-фида ликвидаций Bybit нет (только WebSocket), поэтому
        используем ленту крупных сделок как прокси принудительных закрытий.
        """
        out: list[Liquidation] = []
        try:
            instruments = await self.discover_instruments()
        except Exception:  # noqa: BLE001
            return []
        top = sorted(instruments, key=lambda i: i.turnover_24h, reverse=True)[:12]
        for inst in top:
            try:
                raw = await self.get(
                    "/v5/market/recent-trade", {"category": "linear", "symbol": inst.symbol, "limit": 60}
                )
                res = self._unwrap(raw, "recent-trade") or {}
                for tr in res.get("list", []):
                    usd = float(tr.get("price") or 0) * float(tr.get("size") or 0)
                    if usd < 50_000:
                        continue
                    out.append(
                        Liquidation(
                            symbol=inst.symbol,
                            side=str(tr.get("side", "Buy")),
                            size=usd,
                            qty=float(tr.get("size") or 0),
                            price=float(tr.get("price") or 0),
                            ts_ms=int(tr.get("time") or now_ms()),
                        )
                    )
            except Exception:  # noqa: BLE001
                continue
        out.sort(key=lambda x: x.ts_ms, reverse=True)
        return out[:limit]

    async def get_orderbook(self, symbol: str, depth: int = 25) -> OrderBook:
        raw = await self.get(
            "/v5/market/orderbook", {"category": "linear", "symbol": symbol.upper(), "limit": min(depth, 200)}
        )
        res = self._unwrap(raw, "orderbook") or {}
        return OrderBook(
            symbol=symbol.upper(),
            bids=[(float(p), float(q)) for p, q in res.get("b", [])],
            asks=[(float(p), float(q)) for p, q in res.get("a", [])],
            ts_ms=now_ms(),
        )

    # спот-контекст у чистого биржевого источника отсутствует
    async def get_spot_movers(self, limit: int = 25) -> list[CoinMover]:
        return []

    async def get_trending(self, limit: int = 12) -> list[CoinMover]:
        return []

    async def get_fear_greed(self) -> FearGreed:
        raise DataSourceError("Fear&Greed доступен только в EnrichedSource")

    async def get_global_stats(self) -> GlobalStats:
        raise DataSourceError("Глобальная статистика доступна только в EnrichedSource")

    async def get_news(self, limit: int = 20) -> list[NewsItem]:
        return []


# ════════════════════════════════════════════════════════════════
#  BINANCE USDⓈ-M FUTURES (резерв)
# ════════════════════════════════════════════════════════════════
class BinanceSource(_Http, MarketDataSource):
    name = "binance"
    is_demo = False

    def __init__(self, settings: Settings):
        super().__init__(settings, "https://fapi.binance.com")
        self._instruments: list[Instrument] = []

    async def discover_instruments(self, category: str = "linear") -> list[Instrument]:
        if self._instruments:
            return self._instruments
        raw = await self.get("/fapi/v1/exchangeInfo")
        out: list[Instrument] = []
        for it in raw.get("symbols", []):
            if it.get("quoteAsset") != QUOTE or it.get("contractType") != "PERPETUAL":
                continue
            filters = {f["filterType"]: f for f in it.get("filters", [])}
            tick = float(filters.get("PRICE_FILTER", {}).get("tickSize", 0.0001) or 0.0001)
            step = float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001) or 0.001)
            out.append(
                Instrument(
                    symbol=it["symbol"],
                    base=str(it.get("baseAsset", "")),
                    quote=QUOTE,
                    category="linear",
                    status=str(it.get("status", "TRADING")).title(),
                    price_scale=int(it.get("pricePrecision", 4)),
                    qty_scale=int(it.get("quantityPrecision", 3)),
                    tick_size=tick,
                    qty_step=step,
                    min_qty=float(filters.get("LOT_SIZE", {}).get("minQty", 0) or 0),
                    min_notional=float(filters.get("MIN_NOTIONAL", {}).get("notional", 5) or 5),
                    max_leverage=125,
                )
            )
        self._instruments = out
        return out

    def get_instrument(self, symbol: str) -> Instrument | None:
        symbol = symbol.upper()
        for i in self._instruments:
            if i.symbol == symbol:
                return i
        return None

    async def get_klines(self, symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
        raw = await self.get(
            "/fapi/v1/klines", {"symbol": symbol.upper(), "interval": timeframe, "limit": min(limit, 1500)}
        )
        if not raw:
            raise UnknownSymbol(f"{symbol}: нет свечей на Binance")
        data = [
            {
                "ts": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in raw
        ]
        return self._df(data).tail(limit).reset_index(drop=True)

    async def get_history(
        self, symbol: str, timeframe: str = "1h", bars: int = 1000, max_requests: int = 40
    ) -> pd.DataFrame:
        symbol = symbol.upper()
        rows: list[list] = []
        end: int | None = None
        for _ in range(max(1, max_requests)):
            params = {"symbol": symbol, "interval": timeframe, "limit": 1500}
            if end is not None:
                params["endTime"] = end
            raw = await self.get("/fapi/v1/klines", params)
            if not raw:
                break
            rows.extend(raw)
            if len(rows) >= bars or len(raw) < 2:
                break
            end = min(int(r[0]) for r in raw) - 1
        if not rows:
            raise UnknownSymbol(f"{symbol}: нет истории на Binance")
        data = [
            {"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in rows
        ]
        df = self._df(data)
        df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        return df.tail(bars).reset_index(drop=True)

    async def get_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        raw = await self.get("/fapi/v1/ticker/24hr")
        prem = {}
        try:
            for p in await self.get("/fapi/v1/premiumIndex"):
                prem[p.get("symbol")] = p
        except Exception:  # noqa: BLE001
            prem = {}
        wanted = {s.upper() for s in symbols} if symbols else None
        out: list[Ticker] = []
        for t in raw:
            sym = t.get("symbol", "")
            if wanted and sym not in wanted:
                continue
            last = float(t.get("lastPrice") or 0)
            if last <= 0:
                continue
            p = prem.get(sym, {})
            out.append(
                Ticker(
                    symbol=sym,
                    last=last,
                    price_24h_pct=float(t.get("priceChangePercent") or 0),
                    turnover_24h=float(t.get("quoteVolume") or 0),
                    volume_24h=float(t.get("volume") or 0),
                    high_24h=float(t.get("highPrice") or 0),
                    low_24h=float(t.get("lowPrice") or 0),
                    open_24h=float(t.get("openPrice") or 0),
                    bid=float(t.get("bidPrice") or 0),
                    ask=float(t.get("askPrice") or 0),
                    funding_rate=_none_or_float(p.get("lastFundingRate")),
                    next_funding_ms=_none_or_int(p.get("nextFundingTime")),
                    ts_ms=now_ms(),
                )
            )
        return out

    async def get_funding(self, symbol: str, limit: int = 12) -> list[FundingEntry]:
        raw = await self.get("/fapi/v1/fundingRate", {"symbol": symbol.upper(), "limit": min(limit, 1000)})
        return [
            FundingEntry(ts_ms=int(r.get("fundingTime") or 0), rate=float(r.get("fundingRate") or 0), symbol=symbol.upper())
            for r in raw
        ]

    async def get_recent_liquidations(self, limit: int = 200) -> list[Liquidation]:
        try:
            raw = await self.get("/fapi/v1/allForceOrders", {"limit": min(limit, 1000)})
        except Exception:  # noqa: BLE001
            return []
        out: list[Liquidation] = []
        for r in raw or []:
            o = r.get("order", r)
            price = float(o.get("averagePrice") or o.get("price") or 0)
            qty = float(o.get("executedQty") or o.get("origQty") or 0)
            out.append(
                Liquidation(
                    symbol=o.get("symbol", ""),
                    side="Sell" if o.get("side") == "BUY" else "Buy",
                    size=price * qty,
                    qty=qty,
                    price=price,
                    ts_ms=int(o.get("time") or now_ms()),
                )
            )
        return out

    async def get_orderbook(self, symbol: str, depth: int = 25) -> OrderBook:
        raw = await self.get("/fapi/v1/depth", {"symbol": symbol.upper(), "limit": max(5, min(depth, 1000))})
        return OrderBook(
            symbol=symbol.upper(),
            bids=[(float(p), float(q)) for p, q in raw.get("bids", [])],
            asks=[(float(p), float(q)) for p, q in raw.get("asks", [])],
            ts_ms=now_ms(),
        )

    async def get_spot_movers(self, limit: int = 25) -> list[CoinMover]:
        return []

    async def get_trending(self, limit: int = 12) -> list[CoinMover]:
        return []

    async def get_fear_greed(self) -> FearGreed:
        raise DataSourceError("Fear&Greed доступен только в EnrichedSource")

    async def get_global_stats(self) -> GlobalStats:
        raise DataSourceError("Глобальная статистика доступна только в EnrichedSource")

    async def get_news(self, limit: int = 20) -> list[NewsItem]:
        return []


# ════════════════════════════════════════════════════════════════
#  MEXC CONTRACT (третий источник)
# ════════════════════════════════════════════════════════════════
class MexcSource(_Http, MarketDataSource):
    name = "mexc"
    is_demo = False

    def __init__(self, settings: Settings):
        super().__init__(settings, "https://contract.mexc.com")
        self._instruments: list[Instrument] = []

    async def discover_instruments(self, category: str = "linear") -> list[Instrument]:
        if self._instruments:
            return self._instruments
        raw = await self.get("/api/v1/contract/detail")
        out: list[Instrument] = []
        for it in raw.get("data", []):
            sym = it.get("symbol", "")
            if not sym.endswith("_USDT"):
                continue
            out.append(
                Instrument(
                    symbol=normalize_symbol(sym),
                    base=str(it.get("baseCoin", "")),
                    quote=QUOTE,
                    category="linear",
                    status="Trading" if it.get("state") == 0 else "Closed",
                    price_scale=int(it.get("priceScale", 4) or 4),
                    qty_scale=0,
                    tick_size=float(it.get("minPrice", 0.0001) or 0.0001),
                    qty_step=float(it.get("volMultiple", 1) or 1),
                    min_qty=float(it.get("minVol", 1) or 1),
                    min_notional=5.0,
                    max_leverage=int(it.get("maxLever", 50) or 50),
                )
            )
        self._instruments = out
        return out

    def get_instrument(self, symbol: str) -> Instrument | None:
        symbol = symbol.upper()
        for i in self._instruments:
            if i.symbol == symbol:
                return i
        return None

    @staticmethod
    def _mexc_symbol(symbol: str) -> str:
        s = symbol.upper()
        return s if s.endswith("_USDT") else f"{s.replace(QUOTE, '')}_USDT"

    async def get_klines(self, symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
        raw = await self.get(
            f"/api/v1/contract/kline/{self._mexc_symbol(symbol)}",
            {"interval": _tf_to_mexc(timeframe), "limit": min(limit, 2000)},
        )
        rows = (raw or {}).get("data", [])
        if not rows:
            raise UnknownSymbol(f"{symbol}: нет свечей на MEXC")
        data = [
            {
                "ts": int(r.get("time") or 0) * (1000 if len(str(r.get("time", 0))) <= 10 else 1),
                "open": float(r.get("open") or 0),
                "high": float(r.get("high") or 0),
                "low": float(r.get("low") or 0),
                "close": float(r.get("close") or 0),
                "volume": float(r.get("vol") or r.get("amount") or 0),
            }
            for r in rows
        ]
        return self._df(data).tail(limit).reset_index(drop=True)

    async def get_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        raw = await self.get("/api/v1/contract/ticker")
        wanted = {s.upper() for s in symbols} if symbols else None
        out: list[Ticker] = []
        for t in (raw or {}).get("data", []):
            norm = normalize_symbol(t.get("symbol", ""))
            if wanted and norm not in wanted:
                continue
            last = float(t.get("lastPrice") or 0)
            if last <= 0:
                continue
            out.append(
                Ticker(
                    symbol=norm,
                    last=last,
                    price_24h_pct=float(t.get("riseFallRate") or 0) * 100.0,
                    turnover_24h=float(t.get("amount24") or 0),
                    volume_24h=float(t.get("vol24") or 0),
                    high_24h=float(t.get("high24Price") or 0),
                    low_24h=float(t.get("low24Price") or 0),
                    bid=float(t.get("bidPrice") or 0),
                    ask=float(t.get("askPrice") or 0),
                    funding_rate=_none_or_float(t.get("fundingRate")),
                    next_funding_ms=_none_or_int(t.get("nextFundingTime")),
                    open_interest=_none_or_float(t.get("holdVol")),
                    ts_ms=now_ms(),
                )
            )
        return out

    async def get_funding(self, symbol: str, limit: int = 12) -> list[FundingEntry]:
        raw = await self.get(f"/api/v1/contract/funding_rate/{self._mexc_symbol(symbol)}")
        rows = (raw or {}).get("data", [])
        return [
            FundingEntry(ts_ms=int(r.get("fundingTime") or 0), rate=float(r.get("fundingRate") or 0), symbol=symbol.upper())
            for r in rows[-limit:]
        ]

    async def get_recent_liquidations(self, limit: int = 200) -> list[Liquidation]:
        return []

    async def get_orderbook(self, symbol: str, depth: int = 25) -> OrderBook:
        raw = await self.get(f"/api/v1/contract/depth/{self._mexc_symbol(symbol)}", {"limit": min(depth, 100)})
        data = (raw or {}).get("data", {})
        return OrderBook(
            symbol=symbol.upper(),
            bids=[(float(x.get("price") or 0), float(x.get("quantity") or 0)) for x in data.get("bids", [])],
            asks=[(float(x.get("price") or 0), float(x.get("quantity") or 0)) for x in data.get("asks", [])],
            ts_ms=now_ms(),
        )

    async def get_spot_movers(self, limit: int = 25) -> list[CoinMover]:
        return []

    async def get_trending(self, limit: int = 12) -> list[CoinMover]:
        return []

    async def get_fear_greed(self) -> FearGreed:
        raise DataSourceError("Fear&Greed доступен только в EnrichedSource")

    async def get_global_stats(self) -> GlobalStats:
        raise DataSourceError("Глобальная статистика доступна только в EnrichedSource")

    async def get_news(self, limit: int = 20) -> list[NewsItem]:
        return []


# ════════════════════════════════════════════════════════════════
#  СПОТ-КОНТЕКСТ: CoinGecko + Fear&Greed + новости
# ════════════════════════════════════════════════════════════════
POSITIVE_WORDS = (
    "surge", "soar", "rally", "bullish", "breakout", "record high", "all-time high", "ath",
    "approve", "approved", "adoption", "partnership", "launch", "upgrade", "inflow", "buy",
    "growth", "etf", "accumulate", "halving", "beat", "outperform", "gain", "jump", "pump",
    "ро", "рост", "рекорд", "одобр", "партнёр", "запуск",
)
NEGATIVE_WORDS = (
    "crash", "plunge", "dump", "bearish", "breakdown", "record low", "hack", "exploit", "ban",
    "lawsuit", "sec sues", "outflow", "sell-off", "selloff", "liquidation", "fear", "fraud",
    "delist", "warning", "rug", "insolvency", "bankrupt", "fall", "drop", "slump",
    "крах", "падение", "взлом", "штраф", "бан",
)
KNOWN_BASES = (
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "TON", "DOT", "TRX",
    "MATIC", "LTC", "NEAR", "APT", "SUI", "TIA", "INJ", "SEI", "OP", "ARB", "ATOM", "FIL",
    "AAVE", "UNI", "MKR", "LDO", "RNDR", "FET", "IMX", "GRT", "ALGO", "XLM", "ETC", "ICP",
    "HBAR", "VET", "SAND", "MANA", "GALA", "AXS", "CHZ", "SHIB", "PEPE", "WIF", "BONK",
    "FLOKI", "ORDI", "JUP", "PYTH", "JTO", "ENA", "ETHFI", "ONDO", "STX", "RUNE", "PENDLE",
)


def sentiment_of(text: str) -> float:
    """Простая лексическая оценка сентимента заголовка: -1..+1."""
    t = text.lower()
    score = 0.0
    for w in POSITIVE_WORDS:
        if w in t:
            score += 1.0
    for w in NEGATIVE_WORDS:
        if w in t:
            score -= 1.0
    return max(-1.0, min(1.0, score / 2.5))


def extract_symbols(text: str) -> list[str]:
    """Извлекает тикеры монет из текста новости."""
    up = text.upper()
    found: list[str] = []
    for base in KNOWN_BASES:
        if base in up and base not in found:
            found.append(base)
    return found


class EnrichedSource(MarketDataSource):
    """Биржевой источник + спот-контекст (CoinGecko, Fear&Greed, новости)."""

    is_demo = False

    def __init__(self, settings: Settings, primary: MarketDataSource):
        self.settings = settings
        self.primary = primary
        self.name = primary.name
        self.is_demo = primary.is_demo
        self._cg = _Http(
            settings,
            "https://api.coingecko.com/api/v3",
            headers={"x-cg-demo-api-key": settings.COINGECKO_API_KEY} if settings.COINGECKO_API_KEY else None,
        )
        self._alt = _Http(settings, "https://api.alternative.me")
        self._cc = _Http(settings, "https://min-api.cryptocompare.com")
        self._movers_cache: tuple[float, list[CoinMover]] = (0.0, [])
        self._trending_cache: tuple[float, list[CoinMover]] = (0.0, [])
        self._news_cache: tuple[float, list[NewsItem]] = (0.0, [])
        self._fg_cache: tuple[float, FearGreed | None] = (0.0, None)

    @property
    def mode(self) -> str:
        """Реально выбранный источник (может смениться на demo после фейловера)."""
        return getattr(self.primary, "mode", self.name)

    async def probe(self) -> str:
        if hasattr(self.primary, "probe"):
            await self.primary.probe()
        self.name = self.mode
        self.is_demo = self.primary.is_demo
        return self.mode

    # ── делегирование бирже ──
    async def discover_instruments(self, category: str = "linear") -> list[Instrument]:
        return await self.primary.discover_instruments(category)

    def get_instrument(self, symbol: str) -> Instrument | None:
        return self.primary.get_instrument(symbol)

    async def get_klines(self, symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
        return await self.primary.get_klines(symbol, timeframe, limit)

    async def get_history(
        self, symbol: str, timeframe: str = "1h", bars: int = 1000, max_requests: int = 40
    ) -> pd.DataFrame:
        # пагинацию умеет сам биржевой источник — не глушим её обёрткой
        return await self.primary.get_history(symbol, timeframe, bars, max_requests)

    async def get_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        return await self.primary.get_tickers(symbols)

    async def get_funding(self, symbol: str, limit: int = 12) -> list[FundingEntry]:
        return await self.primary.get_funding(symbol, limit)

    async def get_recent_liquidations(self, limit: int = 200) -> list[Liquidation]:
        return await self.primary.get_recent_liquidations(limit)

    async def get_orderbook(self, symbol: str, depth: int = 25) -> OrderBook:
        return await self.primary.get_orderbook(symbol, depth)

    # ── CoinGecko ──
    async def _cg_markets(self, limit: int = 250) -> list[dict]:
        raw = await self._cg.get(
            "/coins/markets",
            {
                "vs_currency": "usd",
                "order": "volume_desc",
                "per_page": limit,
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "24h",
            },
        )
        return raw if isinstance(raw, list) else []

    async def get_spot_movers(self, limit: int = 25) -> list[CoinMover]:
        ts, cached = self._movers_cache
        if cached and time.time() - ts < 300:
            return cached[:limit]
        try:
            markets = await self._cg_markets(250)
        except Exception as e:  # noqa: BLE001
            logger.debug("CoinGecko movers недоступен: %s", e)
            return cached[:limit]
        out: list[CoinMover] = []
        for rank, m in enumerate(markets, 1):
            sym = str(m.get("symbol", "")).upper()
            if not sym:
                continue
            out.append(
                CoinMover(
                    symbol=normalize_symbol(f"{sym}USDT"),
                    name=str(m.get("name", "")),
                    rank=int(m.get("market_cap_rank") or rank),
                    price=float(m.get("current_price") or 0),
                    price_24h_pct=float(m.get("price_change_percentage_24h") or 0),
                    volume_24h=float(m.get("total_volume") or 0),
                    market_cap=m.get("market_cap"),
                )
            )
        out.sort(key=lambda c: abs(c.price_24h_pct), reverse=True)
        self._movers_cache = (time.time(), out)
        return out[:limit]

    async def get_trending(self, limit: int = 12) -> list[CoinMover]:
        ts, cached = self._trending_cache
        if cached and time.time() - ts < 600:
            return cached[:limit]
        try:
            raw = await self._cg.get("/search/trending")
            coins = raw.get("coins", []) if isinstance(raw, dict) else []
        except Exception as e:  # noqa: BLE001
            logger.debug("CoinGecko trending недоступен: %s", e)
            return cached[:limit]
        out: list[CoinMover] = []
        for i, c in enumerate(coins[:limit], 1):
            item = c.get("item", {})
            sym = str(item.get("symbol", "")).upper()
            data = item.get("data", {}) if isinstance(item.get("data"), dict) else {}
            out.append(
                CoinMover(
                    symbol=normalize_symbol(f"{sym}USDT"),
                    name=str(item.get("name", "")),
                    rank=int(item.get("market_cap_rank") or 999),
                    price=float(data.get("price") or 0),
                    price_24h_pct=float(
                        (data.get("price_change_percentage_24h") or {}).get("usd") or 0
                    ),
                    volume_24h=float(data.get("total_volume") or 0),
                    market_cap=None,
                )
            )
        self._trending_cache = (time.time(), out)
        return out

    async def get_global_stats(self) -> GlobalStats:
        fg = await self.get_fear_greed()
        try:
            g = await self._cg.get("/global")
            data = g.get("data", {}) if isinstance(g, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.debug("CoinGecko global недоступен: %s", e)
            data = {}
        return GlobalStats(
            total_market_cap_usd=float(data.get("total_market_cap", {}).get("usd") or 0),
            total_volume_24h_usd=float(data.get("total_volume", {}).get("usd") or 0),
            btc_dominance=float(data.get("market_cap_percentage", {}).get("btc") or 0),
            eth_dominance=float(data.get("market_cap_percentage", {}).get("eth") or 0),
            market_cap_change_24h_pct=float(data.get("market_cap_change_percentage_24h_usd") or 0),
            fear_greed=fg,
            ts_ms=now_ms(),
        )

    # ── Fear & Greed ──
    async def get_fear_greed(self) -> FearGreed:
        ts, cached = self._fg_cache
        if cached and time.time() - ts < 900:
            return cached
        try:
            raw = await self._alt.get("/fng/", {"limit": 1})
            item = raw.get("data", [{}])[0]
            value = int(item.get("value", 50))
            fg = FearGreed(
                value=value,
                classification=str(item.get("value_classification", "Neutral")),
                ts_ms=int(item.get("timestamp", 0)) * 1000 or now_ms(),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Fear&Greed недоступен: %s", e)
            fg = cached or FearGreed(value=50, classification="Neutral", ts_ms=now_ms())
        self._fg_cache = (time.time(), fg)
        return fg

    # ── новости ──
    async def get_news(self, limit: int = 20) -> list[NewsItem]:
        ts, cached = self._news_cache
        if cached and time.time() - ts < 600:
            return cached[:limit]
        try:
            raw = await self._cc.get("/data/v2/news/", {"lang": "EN", "sortOrder": "latest"})
            items = raw.get("Data", []) if isinstance(raw, dict) else []
        except Exception as e:  # noqa: BLE001
            logger.debug("Новости недоступны: %s", e)
            return cached[:limit]
        out: list[NewsItem] = []
        for n in items[: max(limit, 20)]:
            title = str(n.get("title", ""))
            body = f"{title} {str(n.get('body', ''))[:200]}"
            syms = [str(s).upper() for s in (n.get("categories") or "").split("|") if s][:3]
            syms = syms or extract_symbols(body)
            out.append(
                NewsItem(
                    id=str(n.get("guid") or n.get("id") or title[:32]),
                    ts_ms=int(n.get("published_on") or 0) * 1000 or now_ms(),
                    source=str(n.get("source") or n.get("source_info", {}).get("name") or "cryptocompare"),
                    title=title,
                    url=str(n.get("url") or ""),
                    symbols=syms,
                    sentiment=sentiment_of(body),
                )
            )
        self._news_cache = (time.time(), out)
        return out[:limit]

    async def get_account_ratio(self, symbol: str) -> float | None:
        return await self.primary.get_account_ratio(symbol)

    async def close(self) -> None:
        await self.primary.close()
        for c in (self._cg, self._alt, self._cc):
            await c.close()


# ════════════════════════════════════════════════════════════════
#  ФЕЙЛОВЕР: биржа 1 → биржа 2 → биржа 3 → demo
# ════════════════════════════════════════════════════════════════
class FailoverSource(MarketDataSource):
    """
    Пробует источники по очереди и запоминает первый живой.

    Работает полностью асинхронно: никаких блокирующих проверок при старте,
    поэтому источник можно создавать изнутри event loop (API-хендлеры,
    Telegram-команды). При отказе всех бирж в режиме auto переключаемся на
    демо-рынок и помечаем это в self.mode.
    """

    def __init__(self, settings: Settings, delegates: list[MarketDataSource], allow_demo: bool = True):
        self.settings = settings
        self._delegates = delegates
        self._allow_demo = allow_demo
        self._active: MarketDataSource | None = None
        self._demo: MarketDataSource | None = None
        self.mode = delegates[0].name if delegates else "none"
        self.failures: dict[str, str] = {}

    @property
    def name(self) -> str:
        return self.mode

    @property
    def is_demo(self) -> bool:
        return bool(self._active and self._active.is_demo)

    @property
    def active(self) -> MarketDataSource | None:
        return self._active

    async def probe(self) -> str:
        """Выбирает первый живой источник. Возвращает итоговый режим."""
        if self._active is not None:
            return self.mode
        for cand in self._delegates:
            try:
                df = await cand.get_klines("BTCUSDT", "15m", 5)
                if len(df) >= 3:
                    await cand.discover_instruments()
                    self._active = cand
                    self.mode = cand.name
                    logger.info("Источник данных: %s", cand.name)
                    return self.mode
            except Exception as e:  # noqa: BLE001
                self.failures[cand.name] = str(e)[:120]
                logger.warning("Источник %s недоступен: %s", cand.name, e)
        if not self._allow_demo:
            raise DataSourceError(f"Ни одна биржа недоступна: {self.failures}")
        from src.data.demo import DemoMarketSource

        self._demo = DemoMarketSource(self.settings)
        self._active = self._demo
        self.mode = "demo"
        logger.warning("Биржи недоступны — переключаюсь в DEMO-режим")
        return self.mode

    async def _call(self, method: str, *args, **kwargs):
        if self._active is None:
            await self.probe()
        assert self._active is not None
        try:
            return await getattr(self._active, method)(*args, **kwargs)
        except (UnknownSymbol, DataSourceError) as e:
            # неизвестный символ — не повод менять биржу
            if isinstance(e, UnknownSymbol):
                raise
            self.failures[self.mode] = str(e)[:120]
            self._active = None
            await self.probe()
            return await getattr(self._active, method)(*args, **kwargs)

    async def discover_instruments(self, category: str = "linear") -> list[Instrument]:
        return await self._call("discover_instruments", category)

    def get_instrument(self, symbol: str) -> Instrument | None:
        return self._active.get_instrument(symbol) if self._active else None

    async def get_klines(self, symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
        return await self._call("get_klines", symbol, timeframe, limit)

    async def get_history(
        self, symbol: str, timeframe: str = "1h", bars: int = 1000, max_requests: int = 40
    ) -> pd.DataFrame:
        return await self._call("get_history", symbol, timeframe, bars, max_requests)

    async def get_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        return await self._call("get_tickers", symbols)

    async def get_funding(self, symbol: str, limit: int = 12) -> list[FundingEntry]:
        return await self._call("get_funding", symbol, limit)

    async def get_recent_liquidations(self, limit: int = 200) -> list[Liquidation]:
        return await self._call("get_recent_liquidations", limit)

    async def get_orderbook(self, symbol: str, depth: int = 25) -> OrderBook:
        return await self._call("get_orderbook", symbol, depth)

    async def get_spot_movers(self, limit: int = 25) -> list[CoinMover]:
        return await self._call("get_spot_movers", limit)

    async def get_trending(self, limit: int = 12) -> list[CoinMover]:
        return await self._call("get_trending", limit)

    async def get_fear_greed(self) -> FearGreed:
        return await self._call("get_fear_greed")

    async def get_global_stats(self) -> GlobalStats:
        return await self._call("get_global_stats")

    async def get_news(self, limit: int = 20) -> list[NewsItem]:
        return await self._call("get_news", limit)

    async def get_account_ratio(self, symbol: str) -> float | None:
        return await self._call("get_account_ratio", symbol)

    async def close(self) -> None:
        for d in self._delegates:
            await d.close()
        if self._demo is not None:
            await self._demo.close()


# ════════════════════════════════════════════════════════════════
#  ФАБРИКА
# ════════════════════════════════════════════════════════════════
def build_source(settings: Settings) -> tuple[MarketDataSource, str]:
    """
    Собирает источник данных по MARKET_DATA_MODE (без сетевых вызовов,
    поэтому безопасно вызывать и из синхронного, и из async-кода):
      auto — Bybit → Binance → MEXC, при полном отказе демо-рынок
      live — только биржи (ошибка при недоступности всех)
      demo — только синтетический рынок
    Возвращает (source, mode). Реальный режим уточняется после первого
    запроса: FailoverSource.mode / AppContext.mode.
    """
    mode = (settings.MARKET_DATA_MODE or "auto").lower()

    from src.data.demo import DemoMarketSource

    if mode == "demo":
        logger.info("Режим DEMO: синтетический рынок")
        src = DemoMarketSource(settings)
        return src, "demo"

    failover = FailoverSource(
        settings,
        [BybitSource(settings), BinanceSource(settings), MexcSource(settings)],
        allow_demo=(mode != "live"),
    )
    if mode == "live":
        return failover, "live"
    return EnrichedSource(settings, failover), "auto"


# ── мелкие хелперы ──
def _none_or_float(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _none_or_int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
