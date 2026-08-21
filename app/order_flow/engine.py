from dataclasses import dataclass
from decimal import Decimal

from app.data.orderbook import AggressiveTrade, OrderBookSnapshot


@dataclass(frozen=True, slots=True)
class OrderFlowMetrics:
    book_imbalance: Decimal | None
    aggressive_buy_volume: Decimal
    aggressive_sell_volume: Decimal
    volume_delta: Decimal
    spread: Decimal | None


def analyze_order_flow(
    book: OrderBookSnapshot | None,
    trades: tuple[AggressiveTrade, ...] = (),
    depth: int = 10,
) -> OrderFlowMetrics:
    buy = sum((t.quantity for t in trades if t.taker_side.lower() == "buy"), Decimal(0))
    sell = sum((t.quantity for t in trades if t.taker_side.lower() == "sell"), Decimal(0))
    return OrderFlowMetrics(
        book_imbalance=book.imbalance(depth) if book else None,
        aggressive_buy_volume=buy,
        aggressive_sell_volume=sell,
        volume_delta=buy - sell,
        spread=book.spread if book else None,
    )
