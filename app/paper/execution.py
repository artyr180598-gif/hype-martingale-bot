from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PaperFill:
    side: str
    requested_price: float
    fill_price: float
    quantity: float
    fee: float
    slippage: float


def simulate_market_fill(
    side: str,
    price: float,
    quantity: float,
    fee_rate: float,
    slippage_bps: float = 2.0,
) -> PaperFill:
    if side not in {"LONG", "SHORT"} or price <= 0 or quantity <= 0:
        raise ValueError("invalid_fill_inputs")
    if fee_rate < 0 or slippage_bps < 0:
        raise ValueError("invalid_cost_inputs")
    slip = slippage_bps / 10_000
    fill = price * (1 + slip) if side == "LONG" else price * (1 - slip)
    notional = fill * quantity
    return PaperFill(side, price, fill, quantity, notional * fee_rate, abs(fill - price))
