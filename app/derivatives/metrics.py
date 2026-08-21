from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DerivativesSnapshot:
    funding_rate: Decimal | None
    open_interest: Decimal | None
    open_interest_delta: Decimal | None
    mark_price: Decimal | None
    index_price: Decimal | None
    basis: Decimal | None


def delta(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous in (None, Decimal(0)):
        return None
    return (current - previous) / previous


def basis(mark_price: Decimal | None, index_price: Decimal | None) -> Decimal | None:
    if mark_price is None or index_price in (None, Decimal(0)):
        return None
    return (mark_price - index_price) / index_price
