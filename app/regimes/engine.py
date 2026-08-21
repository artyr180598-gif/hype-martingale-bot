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
    ACCUMULATION = "ACCUMULATION"
    DISTRIBUTION = "DISTRIBUTION"


def classify_regime(trend_score: float, volatility_percentile: float, return_z: float, breakout: bool = False) -> MarketRegime:
    """Deterministic baseline classifier; learned regime models can replace it after validation."""
    if volatility_percentile >= 0.95 and return_z <= -2.5:
        return MarketRegime.PANIC
    if volatility_percentile >= 0.95 and return_z >= 2.5:
        return MarketRegime.EUPHORIA
    if breakout and trend_score >= 0.6:
        return MarketRegime.BREAKOUT
    if breakout and trend_score <= -0.6:
        return MarketRegime.BREAKDOWN
    if volatility_percentile >= 0.75 and abs(trend_score) < 0.35:
        return MarketRegime.HIGH_VOLATILITY_RANGE
    if trend_score >= 0.7:
        return MarketRegime.STRONG_UPTREND
    if trend_score >= 0.2:
        return MarketRegime.WEAK_UPTREND
    if trend_score <= -0.7:
        return MarketRegime.STRONG_DOWNTREND
    if trend_score <= -0.2:
        return MarketRegime.WEAK_DOWNTREND
    return MarketRegime.RANGE
