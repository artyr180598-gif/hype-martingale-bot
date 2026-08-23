"""
Dynamic Leverage Recommendation Engine with Statistical Explanations.
"""
from dataclasses import dataclass


@dataclass
class LeverageRecommendation:
    recommended_leverage: int
    maximum_allowed_leverage: int
    rationale: str
    liquidation_distance_pct: float


class LeverageEngine:
    """
    Computes safe dynamic leverage based on ATR distance, volatility regime, and liquidation buffer.
    """

    @classmethod
    def recommend_leverage(
        cls,
        stop_loss_distance_pct: float,
        volatility_regime: str = "NORMAL",
        max_leverage_ceiling: int = 10,
    ) -> LeverageRecommendation:
        sl_pct = max(0.5, stop_loss_distance_pct)

        # Base leverage calculation ensuring liquidation distance is > 2.5x stop distance
        target_lev = int(60.0 / (sl_pct * 2.0))

        # Adjust for volatility regime
        if volatility_regime in ("EXTREME_VOLATILITY", "HIGH_VOLATILITY"):
            target_lev = min(target_lev, 5)
            rationale = f"Capped at {target_lev}x due to {volatility_regime} to avoid liquidation wick whipsaws"
        elif sl_pct > 4.0:
            target_lev = min(target_lev, 3)
            rationale = f"Reduced to {target_lev}x due to wide structural stop loss ({sl_pct:.1f}%)"
        else:
            rationale = f"Optimal leverage {target_lev}x provides 2.5x buffer between invalidation and liquidation"

        final_lev = max(2, min(max_leverage_ceiling, target_lev))
        liq_dist = (100.0 / final_lev) * 0.95  # Approximate margin buffer

        return LeverageRecommendation(
            recommended_leverage=final_lev,
            maximum_allowed_leverage=max_leverage_ceiling,
            rationale=rationale,
            liquidation_distance_pct=round(liq_dist, 2),
        )
