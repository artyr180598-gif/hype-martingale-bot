"""
Market Regime Models and Timeframe Alignment Data Structures.
"""
from dataclasses import dataclass

from src.config.constants import (
    MarketRegimeType,
    SignalDirection,
    VolatilityRegimeType,
    VolatilityTrend,
)


@dataclass
class MarketRegimeReport:
    symbol: str
    timeframe: str
    timestamp_ms: int
    regime: MarketRegimeType
    volatility_regime: VolatilityRegimeType
    volatility_trend: VolatilityTrend
    confidence: float          # 0.0 to 1.0
    adx_strength: float
    atr_percentile: float
    description: str
    favorable_strategies: list[str]
    unfavorable_strategies: list[str]


@dataclass
class TimeframeBias:
    timeframe: str
    trend_direction: SignalDirection
    regime: MarketRegimeType
    score: float  # -1.0 (strongly bearish) to +1.0 (strongly bullish)


@dataclass
class MultiTimeframeAlignment:
    symbol: str
    macro_bias: TimeframeBias    # 4H or 1D
    medium_bias: TimeframeBias   # 1H
    entry_bias: TimeframeBias    # 15m or 5m
    overall_alignment: str       # "HIGH_BULLISH", "HIGH_BEARISH", "NEUTRAL_RANGE", "CONFLICTING"
    alignment_score: float       # -1.0 to +1.0
    is_counter_trend: bool
    confidence_multiplier: float # 0.5 to 1.25 multiplier for signal confidence
