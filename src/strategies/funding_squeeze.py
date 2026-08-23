"""
Funding and Open Interest Squeeze Strategy.
"""
from typing import Any

from src.config.constants import (
    EntryType,
    MarketRegimeType,
    SignalDirection,
    StrategyStatus,
)
from src.strategies.base import BaseStrategy, StrategySignal


class FundingSqueezeStrategy(BaseStrategy):
    """
    Identifies overcrowded derivatives positioning where retail traders are heavily one-sided,
    creating fuel for cascading liquidations and squeezes.
    """

    def __init__(self):
        super().__init__(
            name="FundingSqueezeStrategy",
            version="1.9.0",
            status=StrategyStatus.PRODUCTION,
            expected_regimes=[
                MarketRegimeType.EUPHORIA,
                MarketRegimeType.PANIC,
                MarketRegimeType.STRONG_UPTREND,
                MarketRegimeType.STRONG_DOWNTREND,
            ],
        )

    def evaluate(self, features: dict[str, Any]) -> StrategySignal:
        symbol = features.get("symbol", "UNKNOWN")
        timeframe = features.get("timeframe", "15m")
        ts = features.get("timestamp_ms", 0)

        close = features.get("close", 1.0)
        atr = features.get("atr_14", close * 0.015)
        funding_rate = features.get("funding_rate", 0.0001)
        funding_z = features.get("funding_z_score", 0.0)
        pos_state = features.get("positioning_state", "NEUTRAL")

        direction = SignalDirection.NO_TRADE
        score = 0.0
        confidence = 0.0
        entry_type = EntryType.MARKET
        entry_price = close
        sl = close
        tp1 = close
        tp2 = close
        tp3 = close
        rr = 0.0
        invalidation = "Funding normalized within baseline"
        reasons: list[str] = []
        warnings: list[str] = []

        # Short Squeeze Condition: Deep negative funding (Z < -2.0) with Short Covering / Accumulation
        if funding_z <= -2.0 or funding_rate < -0.0005:
            direction = SignalDirection.LONG
            entry_type = EntryType.MARKET
            entry_price = close

            sl = entry_price - (atr * 1.5)
            risk_dist = max(entry_price * 0.005, entry_price - sl)

            tp1 = entry_price + (risk_dist * 2.0)
            tp2 = entry_price + (risk_dist * 3.5)
            tp3 = entry_price + (risk_dist * 5.0)
            rr = round((tp1 - entry_price) / risk_dist, 2)

            score = min(95.0, 75.0 + abs(funding_z) * 4.0)
            confidence = 0.86
            invalidation = f"Price loses key swing support (${sl:.2f}) despite negative funding"
            reasons = [
                f"Negative Funding Rate Z-score ({funding_z:.2f}) indicating overcrowded short positions",
                f"Current 8h Funding: {funding_rate*100:.4f}% — shorts paying heavy premium to longs",
                "High probability of aggressive short squeeze cascade",
            ]

        # Long Squeeze / Trap Condition: Extreme positive funding (Z > +2.5) with Long Capitulation Risk
        elif funding_z >= 2.5 or funding_rate > 0.0015:
            direction = SignalDirection.SHORT
            entry_type = EntryType.MARKET
            entry_price = close

            sl = entry_price + (atr * 1.5)
            risk_dist = max(entry_price * 0.005, sl - entry_price)

            tp1 = entry_price - (risk_dist * 2.0)
            tp2 = entry_price - (risk_dist * 3.5)
            tp3 = entry_price - (risk_dist * 5.0)
            rr = round((entry_price - tp1) / risk_dist, 2)

            score = min(95.0, 75.0 + abs(funding_z) * 4.0)
            confidence = 0.86
            invalidation = f"Price invalidates squeeze thesis by breaking higher above ${sl:.2f}"
            reasons = [
                f"Extreme positive Funding Rate Z-score ({funding_z:.2f}) indicating overleveraged retail longs",
                f"Current 8h Funding: {funding_rate*100:.4f}% — unsustainable cost of carry",
                "Derivatives positioning vulnerable to cascading long liquidations",
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
