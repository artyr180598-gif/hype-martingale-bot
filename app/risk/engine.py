from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RiskPlan:
    entry: float
    stop: float
    stop_distance_pct: float
    risk_amount: float
    quantity: float
    notional: float
    leverage: float
    max_loss: float


def build_risk_plan(
    equity: float,
    entry: float,
    stop: float,
    risk_fraction: float,
    leverage_ceiling: float = 10.0,
) -> RiskPlan:
    if equity <= 0 or entry <= 0 or stop <= 0:
        raise ValueError("invalid_risk_inputs")
    if risk_fraction <= 0 or risk_fraction > 0.10:
        raise ValueError("risk_fraction_out_of_bounds")
    distance = abs(entry - stop)
    if distance == 0:
        raise ValueError("zero_stop_distance")
    risk_amount = equity * risk_fraction
    quantity = risk_amount / distance
    notional = quantity * entry
    leverage = min(leverage_ceiling, max(1.0, notional / equity))
    return RiskPlan(
        entry=entry,
        stop=stop,
        stop_distance_pct=distance / entry,
        risk_amount=risk_amount,
        quantity=quantity,
        notional=notional,
        leverage=leverage,
        max_loss=risk_amount,
    )
