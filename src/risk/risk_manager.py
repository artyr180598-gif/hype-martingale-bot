"""
Master Risk Management Guard and Portfolio Controller.
"""

from src.config.constants import RiskProfile
from src.config.settings import settings
from src.core.logging import get_logger
from src.risk.models import TradeRiskPlan
from src.risk.position_sizer import PositionSizer

logger = get_logger("risk.manager")


class RiskManager:
    """
    Evaluates individual and portfolio-level risk before any trade setup is finalized.
    """

    @classmethod
    def evaluate_trade_risk(
        cls,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        account_equity: float,
        open_positions_count: int = 0,
        current_portfolio_risk_pct: float = 0.0,
        risk_profile: RiskProfile = RiskProfile.BALANCED,
        tp1_price: float | None = None,
        tp2_price: float | None = None,
        tp3_price: float | None = None,
        volatility_percentile: float = 50.0,
    ) -> TradeRiskPlan:
        # Calculate sizing
        sizing = PositionSizer.calculate_sizing(
            account_equity=account_equity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            direction=direction,
            risk_profile=risk_profile,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            tp3_price=tp3_price,
            volatility_percentile=volatility_percentile,
        )

        rejections: list[str] = []

        # 1. Max concurrent positions check
        if open_positions_count >= settings.MAX_CONCURRENT_POSITIONS:
            rejections.append(f"Maximum concurrent positions limit ({settings.MAX_CONCURRENT_POSITIONS}) reached")

        # 2. Portfolio cumulative risk check
        projected_risk = current_portfolio_risk_pct + sizing.risk_percentage
        if projected_risk > settings.MAX_PORTFOLIO_RISK_PERCENT:
            rejections.append(f"Projected portfolio risk ({projected_risk:.2f}%) exceeds maximum limit ({settings.MAX_PORTFOLIO_RISK_PERCENT:.2f}%)")

        # 3. Liquidation buffer check
        if sizing.liquidation_buffer_pct < sizing.stop_distance_pct * 1.5:
            rejections.append("Liquidation buffer is too close to stop loss level (< 1.5x buffer)")

        is_approved = len(rejections) == 0

        return TradeRiskPlan(
            symbol=symbol,
            direction=direction,
            risk_profile=risk_profile,
            sizing=sizing,
            is_approved_by_risk_guard=is_approved,
            rejection_reasons=rejections,
        )
