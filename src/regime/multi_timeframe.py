"""
Multi-Timeframe Alignment and Trend Conflict Resolution Engine.
"""
from typing import Any

from src.config.constants import MarketRegimeType, SignalDirection
from src.regime.classifier import MarketRegimeClassifier
from src.regime.models import MultiTimeframeAlignment, TimeframeBias


class MultiTimeframeEngine:
    """
    Computes cross-timeframe alignment across Macro (4H/1D), Medium (1H), and Entry (15m).
    Enforces the rule: Counter-trend setups receive reduced confidence; aligned setups get bonus score.
    """

    @classmethod
    def extract_timeframe_bias(cls, features: dict[str, Any]) -> TimeframeBias:
        if not features:
            return TimeframeBias(
                timeframe="unknown",
                trend_direction=SignalDirection.NO_TRADE,
                regime=MarketRegimeType.UNKNOWN,
                score=0.0,
            )

        tf = features.get("timeframe", "15m")
        regime_report = MarketRegimeClassifier.classify(features)

        close = features.get("close", 1.0)
        ema50 = features.get("ema_50", close)
        ema200 = features.get("ema_200", close)
        structure = features.get("structure_state", "RANGE")

        score = 0.0
        if close > ema50:
            score += 0.3
        else:
            score -= 0.3

        if close > ema200:
            score += 0.3
        else:
            score -= 0.3

        if structure == "BULLISH":
            score += 0.4
        elif structure == "BEARISH":
            score -= 0.4

        score = max(-1.0, min(1.0, score))

        if score >= 0.4:
            direction = SignalDirection.LONG
        elif score <= -0.4:
            direction = SignalDirection.SHORT
        else:
            direction = SignalDirection.NO_TRADE

        return TimeframeBias(
            timeframe=tf,
            trend_direction=direction,
            regime=regime_report.regime,
            score=round(score, 2),
        )

    @classmethod
    def evaluate_alignment(
        cls,
        symbol: str,
        macro_features: dict[str, Any],
        medium_features: dict[str, Any],
        entry_features: dict[str, Any],
    ) -> MultiTimeframeAlignment:
        macro_bias = cls.extract_timeframe_bias(macro_features)
        medium_bias = cls.extract_timeframe_bias(medium_features)
        entry_bias = cls.extract_timeframe_bias(entry_features)

        # Weighted alignment score: Macro (45%), Medium (35%), Entry (20%)
        weighted_score = (
            macro_bias.score * 0.45 + medium_bias.score * 0.35 + entry_bias.score * 0.20
        )

        is_counter_trend = False
        if entry_bias.trend_direction == SignalDirection.LONG and macro_bias.trend_direction == SignalDirection.SHORT or entry_bias.trend_direction == SignalDirection.SHORT and macro_bias.trend_direction == SignalDirection.LONG:
            is_counter_trend = True

        if macro_bias.trend_direction == SignalDirection.LONG and medium_bias.trend_direction == SignalDirection.LONG and entry_bias.trend_direction == SignalDirection.LONG:
            overall = "HIGH_BULLISH"
            conf_mult = 1.20
        elif macro_bias.trend_direction == SignalDirection.SHORT and medium_bias.trend_direction == SignalDirection.SHORT and entry_bias.trend_direction == SignalDirection.SHORT:
            overall = "HIGH_BEARISH"
            conf_mult = 1.20
        elif is_counter_trend:
            overall = "CONFLICTING_COUNTER_TREND"
            conf_mult = 0.65  # Penalty for counter-trend
        else:
            overall = "NEUTRAL_MIXED"
            conf_mult = 0.90

        return MultiTimeframeAlignment(
            symbol=symbol,
            macro_bias=macro_bias,
            medium_bias=medium_bias,
            entry_bias=entry_bias,
            overall_alignment=overall,
            alignment_score=round(weighted_score, 2),
            is_counter_trend=is_counter_trend,
            confidence_multiplier=round(conf_mult, 2),
        )
