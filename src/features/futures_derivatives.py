"""
Futures Derivatives Metrics — Funding Z-Score, Open Interest Delta, and Positioning Divergences.
"""
from typing import Any

import numpy as np


class DerivativesFeatureEngine:
    """
    Computes specialized crypto derivatives indicators.
    """

    @staticmethod
    def calculate_funding_z_score(
        current_funding: float,
        historical_fundings: list | None = None,
        default_mean: float = 0.0001,
        default_std: float = 0.0003,
    ) -> float:
        """
        Standardize funding rate against recent historical distribution.
        """
        if historical_fundings and len(historical_fundings) >= 10:
            mean = float(np.mean(historical_fundings))
            std = float(np.std(historical_fundings))
            if std > 1e-6:
                return float(np.clip((current_funding - mean) / std, -5.0, 5.0))
        # Fallback to standard baseline
        return float(np.clip((current_funding - default_mean) / default_std, -5.0, 5.0))

    @staticmethod
    def analyze_price_oi_relationship(
        price_change_pct: float,
        oi_change_pct: float,
    ) -> dict[str, Any]:
        """
        Classify market positioning state using Price & Open Interest co-movement.
        """
        threshold = 0.5  # % change threshold

        if price_change_pct > threshold and oi_change_pct > threshold:
            state = "LONG_ACCUMULATION"
            interpretation = "Aggressive buyers opening new long positions (Bullish Trend Confirmation)"
            score_bias = 1.0
        elif price_change_pct > threshold and oi_change_pct < -threshold:
            state = "SHORT_COVERING"
            interpretation = "Shorts closing out positions (Short Squeeze / Potential Exhaustion)"
            score_bias = 0.5
        elif price_change_pct < -threshold and oi_change_pct > threshold:
            state = "SHORT_ACCUMULATION"
            interpretation = "Aggressive sellers opening new short positions (Bearish Trend Confirmation)"
            score_bias = -1.0
        elif price_change_pct < -threshold and oi_change_pct < -threshold:
            state = "LONG_LIQUIDATION_UNWINDING"
            interpretation = "Longs forced to close / capitulating (Long Squeeze / Potential Bottom)"
            score_bias = -0.5
        else:
            state = "NEUTRAL"
            interpretation = "Positioning and price changes are balanced"
            score_bias = 0.0

        return {
            "positioning_state": state,
            "interpretation": interpretation,
            "score_bias": score_bias,
            "price_change_pct": round(price_change_pct, 2),
            "oi_change_pct": round(oi_change_pct, 2),
        }

    @staticmethod
    def detect_funding_divergence(
        price_trend: str,
        funding_z_score: float,
    ) -> dict[str, Any]:
        """
        Identify overleveraged traps and squeeze potentials.
        """
        is_divergent = False
        description = "Funding is consistent with price action"
        warning = None

        if price_trend == "DOWNTREND" and funding_z_score > 2.0:
            is_divergent = True
            description = "Extreme positive funding during a downtrend (retail longs trapped)"
            warning = "HIGH_LONG_TRAP_RISK"
        elif price_trend == "UPTREND" and funding_z_score < -2.0:
            is_divergent = True
            description = "Extreme negative funding during an uptrend (fuel for short squeeze)"
            warning = "SHORT_SQUEEZE_FUEL"

        return {
            "funding_divergence": is_divergent,
            "description": description,
            "warning": warning,
            "funding_z_score": round(funding_z_score, 2),
        }
