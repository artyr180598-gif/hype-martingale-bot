"""
CCXT Unified Exchange Adapter with Simulated Fallback.
"""

import ccxt.async_support as ccxt_async

from src.core.exceptions import ExchangeAPIError
from src.core.logging import get_logger
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

logger = get_logger("data.adapters.ccxt")


class CCXTExchangeAdapter(BaseExchangeAdapter):
    """
    CCXT multi-exchange async wrapper.
    """

    def __init__(self, exchange_name: str = "binance", api_key: str = "", api_secret: str = ""):
        self.exchange_name = exchange_name.lower()
        exchange_class = getattr(ccxt_async, self.exchange_name, None)
        if not exchange_class:
            raise ValueError(f"Exchange {exchange_name} not supported by ccxt")

        config = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
        self.exchange = exchange_class(config)

    async def fetch_klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[CandleData]:
        try:
            formatted_symbol = symbol.replace("USDT", "/USDT:USDT") if "/" not in symbol else symbol
            raw_candles = await self.exchange.fetch_ohlcv(
                formatted_symbol, timeframe=timeframe, since=start_time_ms, limit=limit
            )
            candles: list[CandleData] = []
            for row in raw_candles:
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
                    )
                )
            return candles
        except Exception as e:
            raise ExchangeAPIError(self.exchange_name, f"fetch_klines error: {e!s}")

    async def fetch_orderbook(self, symbol: str, limit: int = 50) -> OrderBookData:
        try:
            formatted_symbol = symbol.replace("USDT", "/USDT:USDT") if "/" not in symbol else symbol
            ob = await self.exchange.fetch_order_book(formatted_symbol, limit=limit)
            return OrderBookData(
                symbol=symbol.upper(),
                timestamp_ms=int(ob.get("timestamp") or utc_now_ms()),
                bids=[(float(p), float(q)) for p, q in ob.get("bids", [])],
                asks=[(float(p), float(q)) for p, q in ob.get("asks", [])],
            )
        except Exception as e:
            raise ExchangeAPIError(self.exchange_name, f"fetch_orderbook error: {e!s}")

    async def fetch_ticker(self, symbol: str) -> TickerData:
        try:
            formatted_symbol = symbol.replace("USDT", "/USDT:USDT") if "/" not in symbol else symbol
            t = await self.exchange.fetch_ticker(formatted_symbol)
            last = float(t.get("last") or 0.0)
            return TickerData(
                symbol=symbol.upper(),
                timestamp_ms=int(t.get("timestamp") or utc_now_ms()),
                last_price=last,
                mark_price=float(t.get("mark") or last),
                index_price=float(t.get("index") or last),
                bid_price=float(t.get("bid") or last),
                ask_price=float(t.get("ask") or last),
                volume_24h=float(t.get("baseVolume") or 0.0),
                quote_volume_24h=float(t.get("quoteVolume") or 0.0),
                price_change_24h_percent=float(t.get("percentage") or 0.0),
                high_24h=float(t.get("high") or last),
                low_24h=float(t.get("low") or last),
            )
        except Exception as e:
            raise ExchangeAPIError(self.exchange_name, f"fetch_ticker error: {e!s}")

    async def fetch_funding_rate(self, symbol: str) -> FundingRateData:
        formatted_symbol = symbol.replace("USDT", "/USDT:USDT") if "/" not in symbol else symbol
        try:
            funding = await self.exchange.fetch_funding_rate(formatted_symbol)
            return FundingRateData(
                symbol=symbol.upper(),
                timestamp_ms=int(funding.get("timestamp") or utc_now_ms()),
                funding_rate=float(funding.get("fundingRate") or 0.0001),
                predicted_funding_rate=None,
                funding_time_ms=int(funding.get("fundingTimestamp") or 0),
            )
        except Exception:
            return FundingRateData(
                symbol=symbol.upper(),
                timestamp_ms=utc_now_ms(),
                funding_rate=0.0001,
                predicted_funding_rate=None,
                funding_time_ms=0,
            )

    async def fetch_open_interest(self, symbol: str) -> OpenInterestData:
        return OpenInterestData(
            symbol=symbol.upper(),
            timestamp_ms=utc_now_ms(),
            open_interest=0.0,
            open_interest_usd=0.0,
        )

    async def fetch_taker_ratio(self, symbol: str, period: str = "15m") -> TakerVolumeRatioData | None:
        return None

    async def fetch_liquidation_history(self, symbol: str, limit: int = 50) -> list[LiquidationItem]:
        return []

    async def close(self) -> None:
        await self.exchange.close()
