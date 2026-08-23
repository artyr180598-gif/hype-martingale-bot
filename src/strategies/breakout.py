"""
Breakout and Volatility Squeeze Expansion Strategy.
"""
from typing import Any

from src.config.constants import (
    EntryType,
    MarketRegimeType,
    SignalDirection,
    StrategyStatus,
)
from src.strategies.base import BaseStrategy, StrategySignal


class BreakoutStrategy(BaseStrategy):
    """
    Identifies volatility compression phases (Bollinger / ATR squeeze) followed by
    explosive structural Break of Structure (BOS) volume expansion.
    """

    def __init__(self):
        super().__init__(
            name="BreakoutStrategy",
            version="1.8.0",
            status=StrategyStatus.PRODUCTION,
            expected_regimes=[MarketRegimeType.BREAKOUT, MarketRegimeType.BREAKDOWN],
        )

    def evaluate(self, features: dict[str, Any]) -> StrategySignal:
        symbol = features.get("symbol", "UNKNOWN")
        timeframe = features.get("timeframe", "15m")
        ts = features.get("timestamp_ms", 0)

        close = features.get("close", 1.0)
        atr = features.get("atr_14", close * 0.015)
        bb_width_pct = features.get("bb_width_percentile", 50.0)
        is_squeeze = features.get("is_squeeze", False)
        bos_bullish = features.get("bos_bullish", False)
        bos_bearish = features.get("bos_bearish", False)
        last_sh = features.get("last_swing_high", close * 1.02)
        last_sl = features.get("last_swing_low", close * 0.98)
        vol_regime = features.get("volatility_regime", "NORMAL")

        direction = SignalDirection.NO_TRADE
        score = 0.0
        confidence = 0.0
        entry_type = EntryType.BREAKOUT
        entry_price = close
        sl = close
        tp1 = close
        tp2 = close
        tp3 = close
        rr = 0.0
        invalidation = "No confirmed structural breakout"
        reasons: list[str] = []
        warnings: list[str] = []

        # Bullish Breakout: Structural BOS above Swing High + Compression Squeeze
        if (bos_bullish or close > last_sh) and (is_squeeze or bb_width_pct < 35.0):
            direction = SignalDirection.LONG
            entry_type = EntryType.BREAKOUT
            entry_price = close

            # SL placed back inside range below breakout level or halfway into the consolidation
            sl = max(last_sh - (atr * 0.8), (last_sh + last_sl) / 2)
            risk_dist = max(entry_price * 0.005, entry_price - sl)

            tp1 = entry_price + (risk_dist * 1.8)
            tp2 = entry_price + (risk_dist * 3.0)
            tp3 = entry_price + (risk_dist * 5.0)
            rr = round((tp1 - entry_price) / risk_dist, 2)

            score = min(94.0, 72.0 + (15.0 if is_squeeze else 5.0))
            confidence = 0.85
            invalidation = f"Price re-enters and closes below breakout level (${last_sh:.2f})"
            reasons = [
                f"Confirmed Break of Structure (BOS) above swing resistance ${last_sh:.2f}",
                f"Volatility compression release (BB Width Rank: {bb_width_pct:.1f}%)",
                "Expansion of volume and momentum into new territory",
            ]
            if vol_regime == "EXTREME_VOLATILITY":
                warnings.append("High volatility environment increases risk of fake breakout / liquidity wick")

        # Bearish Breakdown: Structural BOS below Swing Low + Compression Squeeze
        elif (bos_bearish or close < last_sl) and (is_squeeze or bb_width_pct < 35.0):
            direction = SignalDirection.SHORT
            entry_type = EntryType.BREAKOUT
            entry_price = close

            sl = min(last_sl + (atr * 0.8), (last_sh + last_sl) / 2)
            risk_dist = max(entry_price * 0.005, sl - entry_price)

            tp1 = entry_price - (risk_dist * 1.8)
            tp2 = entry_price - (risk_dist * 3.0)
            tp3 = entry_price - (risk_dist * 5.0)
            rr = round((entry_price - tp1) / risk_dist, 2)

            score = min(94.0, 72.0 + (15.0 if is_squeeze else 5.0))
            confidence = 0.85
            invalidation = f"Price re-enters and closes above breakdown level (${last_sl:.2f})"
            reasons = [
                f"Confirmed Break of Structure (BOS) below swing support ${last_sl:.2f}",
                f"Volatility squeeze expansion into bearish discovery (BB Width Rank: {bb_width_pct:.1f}%)",
                "Aggressive selling pushing price out of multi-session range",
            ]

        return StrategySignal(
            strategy_name=self.name,
            strategy_version=self.version,
            symbol=symbol,
            timeframe=timeframe,
            timestamp_ms=ts,
            direction=direction,
            score=round(score, 1),
            confidence=round(confidence, 2),
            entry_type=entry_type,
            entry_price=round(entry_price, 4),
            stop_loss=round(sl, 4),
            take_profit_1=round(tp1, 4),
            take_profit_2=round(tp2, 4),
            take_profit_3=round(tp3, 4),
            risk_reward_ratio=rr,
            invalidation=invalidation,
            reasons=reasons,
            risk_warnings=warnings,
        )
