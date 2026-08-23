"""
Bybit Linear V5 REST Adapter.
"""
from typing import Any

import aiohttp

from src.core.exceptions import ExchangeAPIError
from src.core.logging import get_logger
from src.core.rate_limiter import (
    CircuitBreaker,
    TokenBucketRateLimiter,
    execute_with_retry,
)
from src.core.time_utils import utc_now_ms
from src.data.adapters.base import BaseExchangeAdapter
from src.data.models import (
    CandleData,
    FundingRateData,
    LiquidationItem,
    OpenInterestData,
    OrderBookData,
    TakerVolumeRatioData,
    TickerData,
)

logger = get_logger("data.adapters.bybit")


class BybitLinearAdapter(BaseExchangeAdapter):
    """
    Direct asynchronous adapter for Bybit Linear V5 Perpetual Futures.
    """

    BASE_URL = "https://api.bybit.com"

    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self._session: aiohttp.ClientSession | None = None
        self.rate_limiter = TokenBucketRateLimiter(rate_per_second=10.0, capacity=20.0)
        self.circuit_breaker = CircuitBreaker("bybit_v5", failure_threshold=5, recovery_timeout=20.0)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"User-Agent": "CryptoFuturesQuantPlatform/4.0"}
            if self.api_key:
                headers["X-BAPI-API-KEY"] = self.api_key
            timeout = aiohttp.ClientTimeout(total=10.0)
            self._session = aiohttp.ClientSession(headers=headers, timeout=timeout)
        return self._session

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.BASE_URL}{path}"

        async def _call():
            session = await self._get_session()
            async with session.request(method, url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ExchangeAPIError("bybit", f"HTTP {resp.status}: {text}", status_code=resp.status)
                json_data = await resp.json()
                ret_code = json_data.get("retCode", 0)
                if ret_code != 0:
                    raise ExchangeAPIError("bybit", f"RetCode {ret_code}: {json_data.get('retMsg')}")
                return json_data.get("result", {})

        return await execute_with_retry(
            _call,
            circuit_breaker=self.circuit_breaker,
            rate_limiter=self.rate_limiter,
            max_retries=3,
        )

    def _normalize_interval(self, tf: str) -> str:
        mapping = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "6h": "360",
            "12h": "720",
            "1d": "D",
        }
        return mapping.get(tf.lower(), "15")

    async def fetch_klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[CandleData]:
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol.upper(),
            "interval": self._normalize_interval(timeframe),
            "limit": min(limit, 1000),
        }
        if start_time_ms:
            params["start"] = start_time_ms
        if end_time_ms:
            params["end"] = end_time_ms

        res = await self._request("GET", "/v5/market/kline", params=params)
        raw_list = res.get("list", [])
        candles: list[CandleData] = []
        for row in reversed(raw_list):  # Bybit returns newest first, reverse for chronological
            candles.append(
                CandleData(
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    timestamp_ms=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    quote_volume=float(row[6]),
                    trades_count=0,
                    taker_buy_volume=0.0,
                )
            )
        return candles

    async def fetch_orderbook(self, symbol: str, limit: int = 50) -> OrderBookData:
        params = {"category": "linear", "symbol": symbol.upper(), "limit": limit}
        res = await self._request("GET", "/v5/market/orderbook", params=params)
        return OrderBookData(
            symbol=symbol.upper(),
            timestamp_ms=int(res.get("ts", utc_now_ms())),
            bids=[(float(p), float(q)) for p, q in res.get("b", [])],
            asks=[(float(p), float(q)) for p, q in res.get("a", [])],
        )

    async def fetch_ticker(self, symbol: str) -> TickerData:
        params = {"category": "linear", "symbol": symbol.upper()}
        res = await self._request("GET", "/v5/market/tickers", params=params)
        lst = res.get("list", [])
        if not lst:
            raise ExchangeAPIError("bybit", f"No ticker found for symbol {symbol}")
        t = lst[0]
        return TickerData(
            symbol=symbol.upper(),
            timestamp_ms=utc_now_ms(),
            last_price=float(t.get("lastPrice", 0.0)),
            mark_price=float(t.get("markPrice", 0.0)),
            index_price=float(t.get("indexPrice", 0.0)),
            bid_price=float(t.get("bid1Price", 0.0)),
            ask_price=float(t.get("ask1Price", 0.0)),
            volume_24h=float(t.get("volume24h", 0.0)),
            quote_volume_24h=float(t.get("turnover24h", 0.0)),
            price_change_24h_percent=float(t.get("price24hPcnt", 0.0)) * 100.0,
            high_24h=float(t.get("highPrice24h", 0.0)),
            low_24h=float(t.get("lowPrice24h", 0.0)),
        )

    async def fetch_funding_rate(self, symbol: str) -> FundingRateData:
        params = {"category": "linear", "symbol": symbol.upper()}
        res = await self._request("GET", "/v5/market/tickers", params=params)
        lst = res.get("list", [])
        rate = float(lst[0].get("fundingRate", 0.0001)) if lst else 0.0001
        funding_time = int(lst[0].get("nextFundingTime", 0)) if lst else 0
        return FundingRateData(
            symbol=symbol.upper(),
            timestamp_ms=utc_now_ms(),
            funding_rate=rate,
            predicted_funding_rate=None,
            funding_time_ms=funding_time,
        )

    async def fetch_open_interest(self, symbol: str) -> OpenInterestData:
        params = {"category": "linear", "symbol": symbol.upper(), "intervalTime": "5min", "limit": 1}
        res = await self._request("GET", "/v5/market/open-interest", params=params)
        lst = res.get("list", [])
        oi = float(lst[0].get("openInterest", 0.0)) if lst else 0.0
        return OpenInterestData(
            symbol=symbol.upper(),
            timestamp_ms=utc_now_ms(),
            open_interest=oi,
            open_interest_usd=0.0,
        )

    async def fetch_taker_ratio(self, symbol: str, period: str = "15m") -> TakerVolumeRatioData | None:
        return None

    async def fetch_liquidation_history(self, symbol: str, limit: int = 50) -> list[LiquidationItem]:
        return []

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
