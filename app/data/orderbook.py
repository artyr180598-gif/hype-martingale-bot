from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    symbol: str
    timestamp: datetime
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    sequence: int | None = None

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def imbalance(self, depth: int = 10) -> Decimal | None:
        bid = sum((x.quantity for x in self.bids[:depth]), Decimal(0))
        ask = sum((x.quantity for x in self.asks[:depth]), Decimal(0))
        total = bid + ask
        return None if total == 0 else (bid - ask) / total


@dataclass(frozen=True, slots=True)
class AggressiveTrade:
    symbol: str
    timestamp: datetime
    price: Decimal
    quantity: Decimal
    taker_side: str
