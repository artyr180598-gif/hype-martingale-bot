"""
Position Sizing Engine — Fixed Fractional and Volatility-Adjusted Equity Risk.
"""
from src.config.constants import RiskProfile
from src.config.settings import settings
from src.core.logging import get_logger
from src.risk.models import PositionSizing

logger = get_logger("risk.position_sizer")


class PositionSizer:
    """
    Computes exact trade quantity and margin requirement.
    Strict Rule: Risk is defined by Account Equity & Stop Distance, NEVER arbitrarily by leverage.
    """

    @classmethod
    def calculate_sizing(
        cls,
        account_equity: float,
        entry_price: float,
        stop_loss: float,
        direction: str = "LONG",
        risk_profile: RiskProfile = RiskProfile.BALANCED,
        tp1_price: float | None = None,
        tp2_price: float | None = None,
        tp3_price: float | None = None,
        volatility_percentile: float = 50.0,
    ) -> PositionSizing:
        # Determine allowed risk % based on profile
        if risk_profile == RiskProfile.CONSERVATIVE:
            base_risk_pct = 0.75
        elif risk_profile == RiskProfile.AGGRESSIVE:
            base_risk_pct = 2.50
        else:
            base_risk_pct = settings.MAX_RISK_PER_TRADE_PERCENT  # 1.5%

        # Volatility downscale: if volatility is extreme, reduce risk % by up to 35%
        if volatility_percentile > 80.0:
            base_risk_pct *= 0.65
        elif volatility_percentile > 65.0:
            base_risk_pct *= 0.85

        risk_usd = account_equity * (base_risk_pct / 100.0)
        stop_distance = abs(entry_price - stop_loss)
        stop_dist_pct = (stop_distance / entry_price) * 100.0 if entry_price > 0 else 1.0

        # Safety floor for stop distance to avoid division by zero or extreme sizes
        if stop_distance <= 0 or stop_dist_pct < 0.2:
            stop_distance = entry_price * 0.01  # Default 1%
            stop_dist_pct = 1.0

        # Exact position quantity to risk exactly risk_usd at stop loss
        quantity = risk_usd / stop_distance
        notional_usd = quantity * entry_price

        # Recommended safe leverage: ensures liquidation price is at least 2.5x further than stop loss
        max_safe_lev = max(1, min(settings.MAX_LEVERAGE_CEILING, int(80.0 / (stop_dist_pct * 2.5))))
        leverage = min(settings.MAX_LEVERAGE_CEILING, max_safe_lev)

        margin_required = notional_usd / leverage

        # Estimated liquidation price for cross/isolated USDT-M
        mmr = settings.MAINTENANCE_MARGIN_RATE  # 0.5%
        if direction.upper() == "LONG":
            liq_price = entry_price * (1.0 - (1.0 / leverage) + mmr)
            liq_buffer_pct = ((entry_price - liq_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
        else:
            liq_price = entry_price * (1.0 + (1.0 / leverage) - mmr)
            liq_buffer_pct = ((liq_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0

        # Potential Gains
        gain_tp1 = quantity * abs(tp1_price - entry_price) if tp1_price else risk_usd * 1.5
        gain_tp2 = quantity * abs(tp2_price - entry_price) if tp2_price else None
        gain_tp3 = quantity * abs(tp3_price - entry_price) if tp3_price else None

        return PositionSizing(
            account_equity=round(account_equity, 2),
            risk_percentage=round(base_risk_pct, 2),
            risk_usd=round(risk_usd, 2),
            entry_price=round(entry_price, 4),
            stop_loss=round(stop_loss, 4),
            stop_distance_pct=round(stop_dist_pct, 2),
            quantity=round(quantity, 4),
            notional_value_usd=round(notional_usd, 2),
            recommended_leverage=leverage,
            margin_required_usd=round(margin_required, 2),
            estimated_liquidation_price=round(max(0.0, liq_price), 4),
            liquidation_buffer_pct=round(liq_buffer_pct, 2),
            max_loss_usd=round(risk_usd, 2),
            max_gain_tp1_usd=round(gain_tp1, 2),
            max_gain_tp2_usd=round(gain_tp2, 2) if gain_tp2 else None,
            max_gain_tp3_usd=round(gain_tp3, 2) if gain_tp3 else None,
        )
