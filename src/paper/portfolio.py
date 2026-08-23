"""
Virtual Portfolio and Margin Account Management for Paper Trading.
"""
from pydantic import BaseModel, Field


class PaperPositionState(BaseModel):
    position_id: str
    symbol: str
    side: str  # "LONG" or "SHORT"
    quantity: float
    entry_price: float
    current_price: float
    leverage: int
    margin_locked: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float | None = None
    take_profit_3: float | None = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    commission_paid: float = 0.0
    funding_paid: float = 0.0
    opened_at_ms: int
    status: str = "OPEN"  # OPEN, CLOSED


class VirtualPortfolio(BaseModel):
    initial_balance: float = 10000.0
    cash_balance: float = 10000.0
    total_commission_paid: float = 0.0
    total_funding_paid: float = 0.0
    open_positions: dict[str, PaperPositionState] = Field(default_factory=dict)
    closed_trades_count: int = 0
    winning_trades_count: int = 0
    losing_trades_count: int = 0

    @property
    def margin_used(self) -> float:
        return sum(pos.margin_locked for pos in self.open_positions.values())

    @property
    def available_balance(self) -> float:
        return max(0.0, self.cash_balance - self.margin_used)

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(pos.unrealized_pnl for pos in self.open_positions.values())

    @property
    def total_equity(self) -> float:
        return self.cash_balance + self.total_unrealized_pnl

    @property
    def total_return_pct(self) -> float:
        return ((self.total_equity - self.initial_balance) / self.initial_balance) * 100.0

    @property
    def win_rate_pct(self) -> float:
        return (self.winning_trades_count / max(1, self.closed_trades_count)) * 100.0
