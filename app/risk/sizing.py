from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class PositionSizing:
    quantity: float
    notional: float
    risk_amount: float
    stop_distance: float


def size_from_stop(
    equity: float,
    entry: float,
    invalidation: float,
    risk_fraction: float,
    max_notional: float | None = None,
) -> PositionSizing:
    """Size a position from account risk and structural invalidation.

    Leverage is intentionally absent: leverage is a financing/exposure limit,
    not the source of risk sizing.
    """
    values = (equity, entry, invalidation, risk_fraction)
    if not all(isfinite(value) for value in values):
        raise ValueError("non_finite_risk_input")
    if equity <= 0 or entry <= 0 or invalidation <= 0:
        raise ValueError("non_positive_risk_input")
    if not 0 < risk_fraction <= 1:
        raise ValueError("invalid_risk_fraction")
    stop_distance = abs(entry - invalidation)
    if stop_distance <= 0:
        raise ValueError("zero_stop_distance")
    risk_amount = equity * risk_fraction
    quantity = risk_amount / stop_distance
    notional = quantity * entry
    if max_notional is not None:
        if max_notional <= 0:
            raise ValueError("invalid_max_notional")
        if notional > max_notional:
            notional = max_notional
            quantity = notional / entry
            risk_amount = quantity * stop_distance
    return PositionSizing(quantity, notional, risk_amount, stop_distance)
