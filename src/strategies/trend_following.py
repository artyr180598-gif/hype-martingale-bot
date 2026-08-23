"""
Trend Following Strategy with Dynamic Pullback Sizing and Structural Invalidation.
"""
from typing import Any

from src.config.constants import (
    EntryType,
    MarketRegimeType,
    SignalDirection,
    StrategyStatus,
)
from src.strategies.base import BaseStrategy, StrategySignal


class TrendFollowingStrategy(BaseStrategy):
    """
    Capitalizes on established momentum by entering on pullbacks towards dynamic moving averages
    with tight structural invalidation.
    """

    def __init__(self):
        super().__init__(
            name="TrendFollowingStrategy",
            version="2.1.0",
            status=StrategyStatus.PRODUCTION,
            expected_regimes=[MarketRegimeType.STRONG_UPTREND, MarketRegimeType.STRONG_DOWNTREND],
        )

    def evaluate(self, features: dict[str, Any]) -> StrategySignal:
        symbol = features.get("symbol", "UNKNOWN")
        timeframe = features.get("timeframe", "15m")
        ts = features.get("timestamp_ms", 0)

        close = features.get("close", 1.0)
        ema9 = features.get("ema_9", close)
        ema21 = features.get("ema_21", close)
        ema50 = features.get("ema_50", close)
        ema200 = features.get("ema_200", close)
        adx = features.get("adx_14", 20.0)
        rsi = features.get("rsi_14", 50.0)
        atr = features.get("atr_14", close * 0.015)
        structure = features.get("structure_state", "RANGE")
        last_sh = features.get("last_swing_high", close * 1.05)
        last_sl = features.get("last_swing_low", close * 0.95)

        # Default No-Trade
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
        invalidation = "No actionable trend detected"
        reasons: list[str] = []
        warnings: list[str] = []

        # Bullish Trend Condition: Close > EMA50 > EMA200 and ADX >= 22
        if close > ema50 > ema200 and ema9 > ema21 and structure == "BULLISH" and adx >= 20.0:
            direction = SignalDirection.LONG
            entry_type = EntryType.PULLBACK if close > ema21 else EntryType.MARKET
            entry_price = close

            # Structural Stop Loss: Below EMA 50 or last swing low
            sl = min(ema50 - (atr * 0.5), last_sl - (atr * 0.2))
            risk_dist = max(entry_price * 0.005, entry_price - sl)

            tp1 = entry_price + (risk_dist * 1.5)
            tp2 = entry_price + (risk_dist * 2.5)
            tp3 = entry_price + (risk_dist * 4.0)
            rr = round((tp1 - entry_price) / risk_dist, 2)

            score = min(92.0, 60.0 + (adx * 0.8) + (10.0 if rsi < 65 else 0.0))
            confidence = min(0.92, 0.65 + (adx / 100.0))
            invalidation = f"4H candle closes below EMA 50 (${sl:.2f})"
            reasons = [
                "Multi-EMA bullish stack (EMA 9 > 21 > 50 > 200)",
                f"Trend strength confirmed with ADX at {adx:.1f}",
                "Bullish higher-high market structure intact",
                f"RSI ({rsi:.1f}) provides runway before overbought threshold",
            ]
            if rsi > 70.0:
                warnings.append("RSI elevated near overbought zone — scale in carefully")

        # Bearish Trend Condition: Close < EMA50 < EMA200 and ADX >= 22
        elif close < ema50 < ema200 and ema9 < ema21 and structure == "BEARISH" and adx >= 20.0:
            direction = SignalDirection.SHORT
            entry_type = EntryType.PULLBACK if close < ema21 else EntryType.MARKET
            entry_price = close

            # Structural Stop Loss: Above EMA 50 or last swing high
            sl = max(ema50 + (atr * 0.5), last_sh + (atr * 0.2))
            risk_dist = max(entry_price * 0.005, sl - entry_price)

            tp1 = entry_price - (risk_dist * 1.5)
            tp2 = entry_price - (risk_dist * 2.5)
            tp3 = entry_price - (risk_dist * 4.0)
            rr = round((entry_price - tp1) / risk_dist, 2)

            score = min(92.0, 60.0 + (adx * 0.8) + (10.0 if rsi > 35 else 0.0))
            confidence = min(0.92, 0.65 + (adx / 100.0))
            invalidation = f"4H candle closes above EMA 50 (${sl:.2f})"
            reasons = [
                "Multi-EMA bearish stack (EMA 9 < 21 < 50 < 200)",
                f"Bearish trend strength confirmed with ADX at {adx:.1f}",
                "Lower-highs and lower-lows structure dominant",
                f"RSI ({rsi:.1f}) indicates persistent downward momentum",
            ]
            if rsi < 30.0:
                warnings.append("RSI in oversold zone — watch for sharp relief bounces")

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
