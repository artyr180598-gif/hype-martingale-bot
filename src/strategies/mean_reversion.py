"""
Mean Reversion and VWAP Band Deviation Strategy.
"""
from typing import Any

from src.config.constants import (
    EntryType,
    MarketRegimeType,
    SignalDirection,
    StrategyStatus,
)
from src.strategies.base import BaseStrategy, StrategySignal


class MeanReversionStrategy(BaseStrategy):
    """
    Operates during RANGE and HIGH_VOLATILITY_RANGE regimes when price is statistically stretched
    from the volume-weighted anchor (VWAP / Bollinger Bands) and shows momentum exhaustion.
    """

    def __init__(self):
        super().__init__(
            name="MeanReversionStrategy",
            version="1.6.0",
            status=StrategyStatus.PRODUCTION,
            expected_regimes=[MarketRegimeType.RANGE, MarketRegimeType.HIGH_VOLATILITY_RANGE],
        )

    def evaluate(self, features: dict[str, Any]) -> StrategySignal:
        symbol = features.get("symbol", "UNKNOWN")
        timeframe = features.get("timeframe", "15m")
        ts = features.get("timestamp_ms", 0)

        close = features.get("close", 1.0)
        vwap = features.get("vwap", close)
        vwap_dist = features.get("vwap_dist_pct", 0.0)
        rsi = features.get("rsi_14", 50.0)
        stoch_k = features.get("stoch_k", 50.0)
        atr = features.get("atr_14", close * 0.015)
        structure = features.get("structure_state", "RANGE")
        adx = features.get("adx_14", 20.0)

        direction = SignalDirection.NO_TRADE
        score = 0.0
        confidence = 0.0
        entry_type = EntryType.LIMIT
        entry_price = close
        sl = close
        tp1 = vwap
        tp2 = close
        tp3 = close
        rr = 0.0
        invalidation = "Price within equilibrium band"
        reasons: list[str] = []
        warnings: list[str] = []

        # Mean reversion only safe when ADX is non-trending (< 28) or structure is RANGE
        if adx < 28.0 or structure == "RANGE":
            # Oversold Bounce Long: Extreme negative VWAP deviation + Oversold RSI/Stoch
            if vwap_dist < -2.2 and (rsi < 32.0 or stoch_k < 18.0):
                direction = SignalDirection.LONG
                entry_type = EntryType.MARKET
                entry_price = close

                sl = entry_price - (atr * 1.4)
                risk_dist = max(entry_price * 0.005, entry_price - sl)

                tp1 = vwap  # First target is always the VWAP equilibrium
                tp2 = entry_price + (risk_dist * 2.0)
                tp3 = entry_price + (risk_dist * 3.0)
                rr = round(abs(tp1 - entry_price) / risk_dist, 2)

                score = min(90.0, 68.0 + abs(vwap_dist) * 3.0)
                confidence = 0.78
                invalidation = f"Structural breakdown continuing below swing stop (${sl:.2f})"
                reasons = [
                    f"Price stretched {abs(vwap_dist):.2f}% below Session VWAP",
                    f"RSI ({rsi:.1f}) and Stochastic ({stoch_k:.1f}) in extreme oversold territory",
                    "Range bound market conditions favoring reversion to mean",
                ]

            # Overbought Pullback Short: Extreme positive VWAP deviation + Overbought RSI/Stoch
            elif vwap_dist > 2.2 and (rsi > 68.0 or stoch_k > 82.0):
                direction = SignalDirection.SHORT
                entry_type = EntryType.MARKET
                entry_price = close

                sl = entry_price + (atr * 1.4)
                risk_dist = max(entry_price * 0.005, sl - entry_price)

                tp1 = vwap
                tp2 = entry_price - (risk_dist * 2.0)
                tp3 = entry_price - (risk_dist * 3.0)
                rr = round(abs(entry_price - tp1) / risk_dist, 2)

                score = min(90.0, 68.0 + abs(vwap_dist) * 3.0)
                confidence = 0.78
                invalidation = f"Continued parabolic continuation above stop (${sl:.2f})"
                reasons = [
                    f"Price extended {vwap_dist:.2f}% above Session VWAP",
                    f"RSI ({rsi:.1f}) and Stochastic ({stoch_k:.1f}) displaying overbought exhaustion",
                    "Lack of sustained directional trend indicating high reversion probability",
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
