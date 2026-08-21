from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class MarketEventType(StrEnum):
    TICKER = "ticker"
    TRADE = "trade"
    ORDER_BOOK = "order_book"
    CANDLE = "candle"
    FUNDING = "funding"
    OPEN_INTEREST = "open_interest"
    LIQUIDATION = "liquidation"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_type: MarketEventType
    exchange: str
    symbol: str
    event_time: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class TradeEvent(MarketEvent):
    price: Decimal
    quantity: Decimal
    side: str
    trade_id: str | None = None


@dataclass(frozen=True, slots=True)
class TickerEvent(MarketEvent):
    last_price: Decimal
    mark_price: Decimal | None
    index_price: Decimal | None
    bid_price: Decimal | None
    bid_size: Decimal | None
    ask_price: Decimal | None
    ask_size: Decimal | None
    open_interest: Decimal | None = None
    funding_rate: Decimal | None = None
    basis_rate: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot(MarketEvent):
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    sequence: int | None = None
