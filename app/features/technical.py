from collections.abc import Sequence
from math import log, sqrt


def returns(closes: Sequence[float], periods: int = 1) -> float | None:
    if periods <= 0 or len(closes) <= periods:
        return None
    previous = closes[-periods - 1]
    current = closes[-1]
    if previous == 0:
        return None
    return current / previous - 1.0


def true_range(high: float, low: float, previous_close: float | None) -> float:
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def realized_volatility(closes: Sequence[float], window: int = 20, annualization: float = 365.0) -> float | None:
    if window <= 1 or len(closes) < window + 1:
        return None
    log_returns = [
        log(current / previous)
        for previous, current in zip(closes[-window - 1:-1], closes[-window:])
        if previous > 0 and current > 0
    ]
    if len(log_returns) != window:
        return None
    mean = sum(log_returns) / len(log_returns)
    variance = sum((value - mean) ** 2 for value in log_returns) / (len(log_returns) - 1)
    return sqrt(variance * annualization)


def z_score(value: float, history: Sequence[float], min_samples: int = 20) -> float | None:
    if len(history) < min_samples:
        return None
    mean = sum(history) / len(history)
    variance = sum((item - mean) ** 2 for item in history) / (len(history) - 1)
    std = sqrt(variance)
    return None if std == 0 else (value - mean) / std
