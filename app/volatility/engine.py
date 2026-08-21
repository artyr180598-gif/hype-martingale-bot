from enum import StrEnum


class VolatilityRegime(StrEnum):
    VERY_LOW = "VERY_LOW_VOLATILITY"
    LOW = "LOW_VOLATILITY"
    NORMAL = "NORMAL"
    HIGH = "HIGH_VOLATILITY"
    EXTREME = "EXTREME_VOLATILITY"


def true_range(high: float, low: float, previous_close: float | None) -> float:
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def atr(highs: list[float], lows: list[float], closes: list[float], window: int = 14) -> float | None:
    if window <= 0 or len(highs) != len(lows) or len(lows) != len(closes) or len(closes) < window:
        return None
    ranges = [true_range(h, l, closes[i - 1] if i else None) for i, (h, l) in enumerate(zip(highs, lows))]
    return sum(ranges[-window:]) / window


def classify(percentile: float) -> VolatilityRegime:
    if percentile < 0.10:
        return VolatilityRegime.VERY_LOW
    if percentile < 0.25:
        return VolatilityRegime.LOW
    if percentile < 0.75:
        return VolatilityRegime.NORMAL
    if percentile < 0.95:
        return VolatilityRegime.HIGH
    return VolatilityRegime.EXTREME
