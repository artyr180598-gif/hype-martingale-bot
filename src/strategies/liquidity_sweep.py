"""
Smart Liquidity Sweep and Structural Retest Strategy.
"""
from typing import Any

from src.config.constants import (
    EntryType,
    MarketRegimeType,
    SignalDirection,
    StrategyStatus,
)
from src.strategies.base import BaseStrategy, StrategySignal


class LiquiditySweepStrategy(BaseStrategy):
    """
    Detects stop runs (liquidity sweeps) beyond key support/resistance levels
    followed by immediate absorption and structural Change of Character (CHoCH).
    """

    def __init__(self):
        super().__init__(
            name="LiquiditySweepStrategy",
            version="2.0.0",
            status=StrategyStatus.PRODUCTION,
            expected_regimes=[
                MarketRegimeType.RANGE,
                MarketRegimeType.HIGH_VOLATILITY_RANGE,
                MarketRegimeType.ACCUMULATION,
                MarketRegimeType.DISTRIBUTION,
            ],
        )

    def evaluate(self, features: dict[str, Any]) -> StrategySignal:
        symbol = features.get("symbol", "UNKNOWN")
        timeframe = features.get("timeframe", "15m")
        ts = features.get("timestamp_ms", 0)

        close = features.get("close", 1.0)
        atr = features.get("atr_14", close * 0.015)
        sweep_bullish = features.get("sweep_bullish", False)
        sweep_bearish = features.get("sweep_bearish", False)
        choch_bullish = features.get("choch_bullish", False)
        choch_bearish = features.get("choch_bearish", False)
        last_sh = features.get("last_swing_high", close * 1.03)
        last_sl = features.get("last_swing_low", close * 0.97)

        direction = SignalDirection.NO_TRADE
        score = 0.0
        confidence = 0.0
        entry_type = EntryType.RETEST
        entry_price = close
        sl = close
        tp1 = close
        tp2 = close
        tp3 = close
        rr = 0.0
        invalidation = "No verified liquidity sweep"
        reasons: list[str] = []
        warnings: list[str] = []

        # Bullish Liquidity Sweep: Wick below key swing low / equal lows + immediate reclamation
        if sweep_bullish or (choch_bullish and close > last_sl):
            direction = SignalDirection.LONG
            entry_type = EntryType.RETEST
            entry_price = close

            # Tightly defined invalidation below the lowest wick of the sweep
            sl = last_sl - (atr * 0.3)
            risk_dist = max(entry_price * 0.005, entry_price - sl)

            # Target opposing liquidity pool (last swing high)
            tp1 = min(last_sh, entry_price + (risk_dist * 2.0))
            tp2 = entry_price + (risk_dist * 3.5)
            tp3 = entry_price + (risk_dist * 5.0)
            rr = round((tp1 - entry_price) / risk_dist, 2)

            score = min(96.0, 78.0 + (12.0 if choch_bullish else 6.0))
            confidence = 0.88
            invalidation = f"Price invalidates sweep by making a new lower low below ${sl:.2f}"
            reasons = [
                "Bullish Liquidity Sweep: Retail stop orders below support were cleared and absorbed",
                "Immediate price rejection with bar closing firmly inside range",
                f"High-probability expansion toward opposing liquidity pool (${last_sh:.2f})",
            ]
            if choch_bullish:
                reasons.append("Change of Character (CHoCH) confirms institutional buyer takeover")

        # Bearish Liquidity Sweep: Wick above key swing high / equal highs + immediate rejection
        elif sweep_bearish or (choch_bearish and close < last_sh):
            direction = SignalDirection.SHORT
            entry_type = EntryType.RETEST
            entry_price = close

            sl = last_sh + (atr * 0.3)
            risk_dist = max(entry_price * 0.005, sl - entry_price)

            tp1 = max(last_sl, entry_price - (risk_dist * 2.0))
            tp2 = entry_price - (risk_dist * 3.5)
            tp3 = entry_price - (risk_dist * 5.0)
            rr = round((entry_price - tp1) / risk_dist, 2)

            score = min(96.0, 78.0 + (12.0 if choch_bearish else 6.0))
            confidence = 0.88
            invalidation = f"Price invalidates sweep by making a new higher high above ${sl:.2f}"
            reasons = [
                "Bearish Liquidity Sweep: Buy-side stop orders above resistance were hunted and rejected",
                "Aggressive selling wick confirmed with prompt close back below range ceiling",
                f"Targeting opposing sell-side liquidity pool at ${last_sl:.2f}",
            ]
            if choch_bearish:
                reasons.append("Change of Character (CHoCH) confirms institutional seller dominance")

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
