"""
Market Data Schemas and Data Transfer Objects.
"""
from pydantic import BaseModel, Field

from src.config.constants import DataQualityStatus


class CandleData(BaseModel):
    symbol: str
    timeframe: str
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float = 0.0
    trades_count: int = 0
    taker_buy_volume: float = 0.0

    @property
    def hl2(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def hlc3(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def ohlc4(self) -> float:
        return (self.open + self.high + self.low + self.close) / 4.0


class OrderBookLevel(BaseModel):
    price: float
    amount: float


class OrderBookData(BaseModel):
    symbol: str
    timestamp_ms: int
    bids: list[tuple[float, float]]  # List of [price, size]
    asks: list[tuple[float, float]]  # List of [price, size]

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def spread(self) -> float:
        return max(0.0, self.best_ask - self.best_bid)

    @property
    def spread_percent(self) -> float:
        mid = (self.best_ask + self.best_bid) / 2.0
        return (self.spread / mid * 100.0) if mid > 0 else 0.0

    @property
    def bid_depth_usd(self) -> float:
        return sum(p * s for p, s in self.bids[:20])

    @property
    def ask_depth_usd(self) -> float:
        return sum(p * s for p, s in self.asks[:20])

    @property
    def orderbook_imbalance(self) -> float:
        """Imbalance score from -1.0 (pure ask/sell pressure) to +1.0 (pure bid/buy pressure)."""
        total = self.bid_depth_usd + self.ask_depth_usd
        if total == 0:
            return 0.0
        return (self.bid_depth_usd - self.ask_depth_usd) / total


class TickerData(BaseModel):
    symbol: str
    timestamp_ms: int
    last_price: float
    mark_price: float
    index_price: float
    bid_price: float
    ask_price: float
    volume_24h: float
    quote_volume_24h: float
    price_change_24h_percent: float
    high_24h: float
    low_24h: float


class FundingRateData(BaseModel):
    symbol: str
    timestamp_ms: int
    funding_rate: float
    predicted_funding_rate: float | None = None
    funding_time_ms: int


class OpenInterestData(BaseModel):
    symbol: str
    timestamp_ms: int
    open_interest: float
    open_interest_usd: float


class TakerVolumeRatioData(BaseModel):
    symbol: str
    timestamp_ms: int
    buy_ratio: float
    sell_ratio: float
    buy_vol: float
    sell_vol: float


class LiquidationItem(BaseModel):
    symbol: str
    timestamp_ms: int
    side: str  # BUY (short liquidated) or SELL (long liquidated)
    price: float
    quantity: float
    usd_value: float


class DataQualityReport(BaseModel):
    symbol: str
    timeframe: str
    total_candles: int
    missing_candles_count: int
    duplicate_candles_count: int
    invalid_price_count: int
    gap_percentage: float
    quality_score: float  # 0.0 to 1.0 (1.0 = perfect)
    status: DataQualityStatus
    is_acceptable_for_trading: bool
    issues: list[str] = Field(default_factory=list)
