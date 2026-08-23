"""
Risk Engine Models and Trade Allocation Plans.
"""
from pydantic import BaseModel

from src.config.constants import RiskProfile


class PositionSizing(BaseModel):
    account_equity: float
    risk_percentage: float
    risk_usd: float
    entry_price: float
    stop_loss: float
    stop_distance_pct: float
    quantity: float
    notional_value_usd: float
    recommended_leverage: int
    margin_required_usd: float
    estimated_liquidation_price: float
    liquidation_buffer_pct: float
    max_loss_usd: float
    max_gain_tp1_usd: float
    max_gain_tp2_usd: float | None = None
    max_gain_tp3_usd: float | None = None


class TradeRiskPlan(BaseModel):
    symbol: str
    direction: str
    risk_profile: RiskProfile
    sizing: PositionSizing
    tp1_allocation_pct: float = 40.0
    tp2_allocation_pct: float = 35.0
    tp3_allocation_pct: float = 25.0
    is_approved_by_risk_guard: bool = True
    rejection_reasons: list[str] = []
