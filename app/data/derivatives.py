from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DerivativesSnapshot:
    symbol: str
    timestamp: datetime
    funding_rate: Decimal | None = None
    predicted_funding_rate: Decimal | None = None
    open_interest: Decimal | None = None
    open_interest_delta: Decimal | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    basis: Decimal | None = None
    long_short_ratio: Decimal | None = None
    taker_buy_volume: Decimal | None = None
    taker_sell_volume: Decimal | None = None


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    symbol: str
    timestamp: datetime
    side: str
    price: Decimal
    quantity: Decimal
    source: str
