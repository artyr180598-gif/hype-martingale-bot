"""
Base Abstract Exchange Adapter Interface.
"""
from abc import ABC, abstractmethod

from src.data.models import (
    CandleData,
    FundingRateData,
    LiquidationItem,
    OpenInterestData,
    OrderBookData,
    TakerVolumeRatioData,
    TickerData,
)


class BaseExchangeAdapter(ABC):
    """Abstract interface for all exchange integrations."""

    @abstractmethod
    async def fetch_klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[CandleData]:
        """Fetch historical candlestick bars."""

    @abstractmethod
    async def fetch_orderbook(self, symbol: str, limit: int = 50) -> OrderBookData:
        """Fetch real-time order book snapshot."""

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> TickerData:
        """Fetch 24hr ticker prices, volume, and mark/index prices."""

    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> FundingRateData:
        """Fetch current funding rate and predicted rate."""

    @abstractmethod
    async def fetch_open_interest(self, symbol: str) -> OpenInterestData:
        """Fetch current open interest."""

    @abstractmethod
    async def fetch_taker_ratio(self, symbol: str, period: str = "15m") -> TakerVolumeRatioData | None:
        """Fetch taker buy/sell ratio."""

    @abstractmethod
    async def fetch_liquidation_history(self, symbol: str, limit: int = 50) -> list[LiquidationItem]:
        """Fetch recent liquidation orders."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up network sessions and resources."""
