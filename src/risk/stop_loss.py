"""
Dynamic Structural Stop Loss and Multi-Target Take Profit Engines.
"""


class StopLossEngine:
    """
    Computes volatility-adjusted and structural stop-loss levels.
    """

    @staticmethod
    def calculate_atr_stop(
        entry_price: float,
        atr: float,
        direction: str = "LONG",
        atr_multiplier: float = 1.5,
    ) -> float:
        """Standard ATR-based stop loss level."""
        if direction.upper() == "LONG":
            return max(0.0001, entry_price - (atr * atr_multiplier))
        else:
            return entry_price + (atr * atr_multiplier)

    @staticmethod
    def calculate_structural_stop(
        entry_price: float,
        swing_level: float,
        atr: float,
        direction: str = "LONG",
        buffer_atr_mult: float = 0.3,
    ) -> float:
        """Structural stop placed beyond key swing level plus volatility buffer."""
        if direction.upper() == "LONG":
            return min(entry_price * 0.995, swing_level - (atr * buffer_atr_mult))
        else:
            return max(entry_price * 1.005, swing_level + (atr * buffer_atr_mult))


class TakeProfitEngine:
    """
    Computes multi-target take profit scale-outs based on Risk/Reward multiples and liquidity pools.
    """

    @staticmethod
    def calculate_targets(
        entry_price: float,
        stop_loss: float,
        direction: str = "LONG",
        tp1_r: float = 1.5,
        tp2_r: float = 2.5,
        tp3_r: float = 4.0,
    ) -> tuple[float, float, float]:
        risk_dist = abs(entry_price - stop_loss)
        if direction.upper() == "LONG":
            tp1 = entry_price + (risk_dist * tp1_r)
            tp2 = entry_price + (risk_dist * tp2_r)
            tp3 = entry_price + (risk_dist * tp3_r)
        else:
            tp1 = max(0.0001, entry_price - (risk_dist * tp1_r))
            tp2 = max(0.0001, entry_price - (risk_dist * tp2_r))
            tp3 = max(0.0001, entry_price - (risk_dist * tp3_r))
        return round(tp1, 4), round(tp2, 4), round(tp3, 4)
