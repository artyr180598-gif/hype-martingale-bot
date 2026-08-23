"""
Binance USDT-M Futures Official REST Adapter.
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

logger = get_logger("data.adapters.binance")


class BinanceFuturesAdapter(BaseExchangeAdapter):
    """
    Direct asynchronous adapter for Binance USDS-Margined Futures (fapi).
    """

    BASE_URL = "https://fapi.binance.com"
    DATA_URL = "https://fapi.binance.com/futures/data"

    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self._session: aiohttp.ClientSession | None = None
        self.rate_limiter = TokenBucketRateLimiter(rate_per_second=20.0, capacity=50.0)
        self.circuit_breaker = CircuitBreaker("binance_fapi", failure_threshold=5, recovery_timeout=20.0)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"User-Agent": "CryptoFuturesQuantPlatform/4.0"}
            if self.api_key:
                headers["X-MBX-APIKEY"] = self.api_key
            timeout = aiohttp.ClientTimeout(total=10.0)
            self._session = aiohttp.ClientSession(headers=headers, timeout=timeout)
        return self._session

    async def _request(self, method: str, url: str, params: dict[str, Any] | None = None) -> Any:
        async def _call():
            session = await self._get_session()
            async with session.request(method, url, params=params) as resp:
                if resp.status == 429:
                    raise ExchangeAPIError("binance", "Rate limit exceeded (HTTP 429)", status_code=429)
                if resp.status != 200:
                    text = await resp.text()
                    raise ExchangeAPIError("binance", f"HTTP {resp.status}: {text}", status_code=resp.status)
                return await resp.json()

        return await execute_with_retry(
            _call,
            circuit_breaker=self.circuit_breaker,
            rate_limiter=self.rate_limiter,
            max_retries=3,
        )

    async def fetch_klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[CandleData]:
        url = f"{self.BASE_URL}/fapi/v1/klines"
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": timeframe,
            "limit": min(limit, 1000),
        }
        if start_time_ms:
            params["startTime"] = start_time_ms
        if end_time_ms:
            params["endTime"] = end_time_ms

        data = await self._request("GET", url, params=params)
        candles: list[CandleData] = []
        for row in data:
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
                    quote_volume=float(row[7]),
                    trades_count=int(row[8]),
                    taker_buy_volume=float(row[9]),
                )
            )
        return candles

    async def fetch_orderbook(self, symbol: str, limit: int = 50) -> OrderBookData:
        url = f"{self.BASE_URL}/fapi/v1/depth"
        params = {"symbol": symbol.upper(), "limit": limit}
        data = await self._request("GET", url, params=params)
        return OrderBookData(
            symbol=symbol.upper(),
            timestamp_ms=int(data.get("T", utc_now_ms())),
            bids=[(float(p), float(q)) for p, q in data.get("bids", [])],
            asks=[(float(p), float(q)) for p, q in data.get("asks", [])],
        )

    async def fetch_ticker(self, symbol: str) -> TickerData:
        url_24hr = f"{self.BASE_URL}/fapi/v1/ticker/24hr"
        url_prem = f"{self.BASE_URL}/fapi/v1/premiumIndex"
        t_data = await self._request("GET", url_24hr, params={"symbol": symbol.upper()})
        p_data = await self._request("GET", url_prem, params={"symbol": symbol.upper()})

        return TickerData(
            symbol=symbol.upper(),
            timestamp_ms=int(t_data.get("closeTime", utc_now_ms())),
            last_price=float(t_data.get("lastPrice", 0.0)),
            mark_price=float(p_data.get("markPrice", 0.0)),
            index_price=float(p_data.get("indexPrice", 0.0)),
            bid_price=float(t_data.get("bidPrice", 0.0)),
            ask_price=float(t_data.get("askPrice", 0.0)),
            volume_24h=float(t_data.get("volume", 0.0)),
            quote_volume_24h=float(t_data.get("quoteVolume", 0.0)),
            price_change_24h_percent=float(t_data.get("priceChangePercent", 0.0)),
            high_24h=float(t_data.get("highPrice", 0.0)),
            low_24h=float(t_data.get("lowPrice", 0.0)),
        )

    async def fetch_funding_rate(self, symbol: str) -> FundingRateData:
        url = f"{self.BASE_URL}/fapi/v1/premiumIndex"
        data = await self._request("GET", url, params={"symbol": symbol.upper()})
        return FundingRateData(
            symbol=symbol.upper(),
            timestamp_ms=int(data.get("time", utc_now_ms())),
            funding_rate=float(data.get("lastFundingRate", 0.0)),
            predicted_funding_rate=None,
            funding_time_ms=int(data.get("nextFundingTime", 0)),
        )

    async def fetch_open_interest(self, symbol: str) -> OpenInterestData:
        url = f"{self.BASE_URL}/fapi/v1/openInterest"
        data = await self._request("GET", url, params={"symbol": symbol.upper()})
        oi_val = float(data.get("openInterest", 0.0))
        # Approximate USD value using current price if needed
        return OpenInterestData(
            symbol=symbol.upper(),
            timestamp_ms=int(data.get("time", utc_now_ms())),
            open_interest=oi_val,
            open_interest_usd=0.0,
        )

    async def fetch_taker_ratio(self, symbol: str, period: str = "15m") -> TakerVolumeRatioData | None:
        url = f"{self.DATA_URL}/takerlongshortRatio"
        params = {"symbol": symbol.upper(), "period": period, "limit": 1}
        try:
            data = await self._request("GET", url, params=params)
            if data and len(data) > 0:
                item = data[0]
                return TakerVolumeRatioData(
                    symbol=symbol.upper(),
                    timestamp_ms=int(item.get("timestamp", utc_now_ms())),
                    buy_ratio=float(item.get("buyRatio", 0.5)),
                    sell_ratio=float(item.get("sellRatio", 0.5)),
                    buy_vol=float(item.get("buyVol", 0.0)),
                    sell_vol=float(item.get("sellVol", 0.0)),
                )
        except Exception as e:
            logger.debug("Taker ratio unavailable", symbol=symbol, error=str(e))
        return None

    async def fetch_liquidation_history(self, symbol: str, limit: int = 50) -> list[LiquidationItem]:
        # Liquidations can be tracked from stream or rest if supported
        return []

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
