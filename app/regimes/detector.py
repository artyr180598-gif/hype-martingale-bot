from dataclasses import dataclass
from enum import StrEnum


class MarketRegime(StrEnum):
    STRONG_UPTREND = "STRONG_UPTREND"
    WEAK_UPTREND = "WEAK_UPTREND"
    RANGE = "RANGE"
    WEAK_DOWNTREND = "WEAK_DOWNTREND"
    STRONG_DOWNTREND = "STRONG_DOWNTREND"
    HIGH_VOLATILITY_RANGE = "HIGH_VOLATILITY_RANGE"
    PANIC = "PANIC"
    EUPHORIA = "EUPHORIA"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RegimeInput:
    trend_score: float
    volatility_percentile: float
    return_lookback: float
    volume_z: float
    oi_change: float = 0.0
    breadth: float = 0.0


def detect_regime(value: RegimeInput) -> MarketRegime:
    """Classify from contemporaneous features only; no future observations."""
    if value.volatility_percentile >= 0.95 and value.return_lookback <= -0.08:
        return MarketRegime.PANIC
    if value.volatility_percentile >= 0.95 and value.return_lookback >= 0.08:
        return MarketRegime.EUPHORIA
    if value.trend_score >= 0.65:
        return MarketRegime.BREAKOUT if value.volume_z >= 2.0 else MarketRegime.STRONG_UPTREND
    if value.trend_score >= 0.20:
        return MarketRegime.WEAK_UPTREND
    if value.trend_score <= -0.65:
        return MarketRegime.BREAKDOWN if value.volume_z >= 2.0 else MarketRegime.STRONG_DOWNTREND
    if value.trend_score <= -0.20:
        return MarketRegime.WEAK_DOWNTREND
    if value.volatility_percentile >= 0.80:
        return MarketRegime.HIGH_VOLATILITY_RANGE
    return MarketRegime.RANGE
