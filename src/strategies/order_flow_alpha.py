"""
Microstructural Order Flow and Taker Imbalance Strategy.
"""
from typing import Any

from src.config.constants import (
    EntryType,
    MarketRegimeType,
    SignalDirection,
    StrategyStatus,
)
from src.strategies.base import BaseStrategy, StrategySignal


class OrderFlowAlphaStrategy(BaseStrategy):
    """
    Exploits high-frequency microstructural imbalances, aggressive taker flows,
    and resting limit order absorption.
    """

    def __init__(self):
        super().__init__(
            name="OrderFlowAlphaStrategy",
            version="1.5.0",
            status=StrategyStatus.PRODUCTION,
            expected_regimes=[
                MarketRegimeType.STRONG_UPTREND,
                MarketRegimeType.STRONG_DOWNTREND,
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
        imbalance = features.get("orderbook_imbalance", 0.0)
        cvd_div = features.get("cvd_divergence", 0.0)
        suspicious = features.get("suspicious_liquidity", False)

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
        invalidation = "Order flow balanced"
        reasons: list[str] = []
        warnings: list[str] = []

        if suspicious:
            warnings.append("Suspicious liquidity behavior detected in orderbook — wide spread & rapid wall shift")

        # Bullish Order Flow: Heavy Bid Book Imbalance (> +0.35) and Positive CVD Absorption
        if imbalance >= 0.35 and cvd_div >= 0 and not suspicious:
            direction = SignalDirection.LONG
            entry_type = EntryType.MARKET
            entry_price = close

            sl = entry_price - (atr * 1.0)
            risk_dist = max(entry_price * 0.005, entry_price - sl)

            tp1 = entry_price + (risk_dist * 1.6)
            tp2 = entry_price + (risk_dist * 2.8)
            tp3 = entry_price + (risk_dist * 4.2)
            rr = round((tp1 - entry_price) / risk_dist, 2)

            score = min(92.0, 70.0 + (imbalance * 30.0))
            confidence = 0.82
            invalidation = f"Order book flips to net ask-dominant or price drops below ${sl:.2f}"
            reasons = [
                f"Significant bid depth imbalance (+{imbalance*100:.1f}%) supporting price",
                "Cumulative Volume Delta (CVD) confirms institutional buyer aggression",
                "Passive liquidity absorbing sell orders efficiently",
            ]

        # Bearish Order Flow: Heavy Ask Book Imbalance (< -0.35) and Negative CVD
        elif imbalance <= -0.35 and cvd_div <= 0 and not suspicious:
            direction = SignalDirection.SHORT
            entry_type = EntryType.MARKET
            entry_price = close

            sl = entry_price + (atr * 1.0)
            risk_dist = max(entry_price * 0.005, sl - entry_price)

            tp1 = entry_price - (risk_dist * 1.6)
            tp2 = entry_price - (risk_dist * 2.8)
            tp3 = entry_price - (risk_dist * 4.2)
            rr = round((entry_price - tp1) / risk_dist, 2)

            score = min(92.0, 70.0 + (abs(imbalance) * 30.0))
            confidence = 0.82
            invalidation = f"Order book flips to net bid-dominant or price climbs above ${sl:.2f}"
            reasons = [
                f"Significant ask depth imbalance ({imbalance*100:.1f}%) creating overhead resistance",
                "CVD divergence confirms persistent taker selling pressure",
                "Bid liquidity thinning out on microstructural timeframe",
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
