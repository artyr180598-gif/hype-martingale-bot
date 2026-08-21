from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PaperPosition:
    symbol: str
    side: str
    quantity: float
    entry: float
    leverage: float


@dataclass(frozen=True, slots=True)
class PaperFill:
    price: float
    fee: float
    slippage: float


def simulate_market_fill(price: float, quantity: float, side: str, fee_rate: float, slippage_bps: float) -> PaperFill:
    if price <= 0 or quantity <= 0 or side not in {"LONG", "SHORT"}:
        raise ValueError("invalid_fill")
    slip = slippage_bps / 10_000
    executed = price * (1 + slip if side == "LONG" else 1 - slip)
    return PaperFill(executed, executed * quantity * fee_rate, slip)
