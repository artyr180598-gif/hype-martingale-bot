from dataclasses import dataclass
from enum import StrEnum


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"
    WATCH = "WATCH"


@dataclass(frozen=True, slots=True)
class SignalInput:
    trend: float
    structure: float
    momentum: float
    volume: float
    volatility: float
    order_flow: float
    open_interest: float
    funding: float
    liquidations: float
    breadth: float
    news: float
    data_quality: float = 1.0


@dataclass(frozen=True, slots=True)
class Signal:
    direction: Direction
    score: float
    confidence: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


WEIGHTS = {
    "trend": 15,
    "structure": 15,
    "momentum": 10,
    "volume": 10,
    "volatility": 10,
    "order_flow": 15,
    "open_interest": 10,
    "funding": 5,
    "liquidations": 5,
    "breadth": 5,
    "news": 5,
}


def _tier(score: float) -> str:
    if score >= 90:
        return "EXTREME"
    if score >= 80:
        return "HIGH"
    if score >= 70:
        return "VALID"
    if score >= 60:
        return "WATCH"
    return "LOW"


def score_signal(values: SignalInput, direction: Direction) -> Signal:
    raw = sum(getattr(values, name) * weight for name, weight in WEIGHTS.items()) / 100
    score = max(0.0, min(100.0, raw * max(0.0, min(1.0, values.data_quality))))
    warnings: list[str] = []
    reasons: list[str] = []
    if values.data_quality < 0.90:
        warnings.append("data_quality_degraded")
    if direction is Direction.NO_TRADE or score < 60 or values.data_quality < 0.70:
        return Signal(Direction.NO_TRADE, score, "LOW", tuple(reasons), tuple(warnings + ["insufficient_edge"]))
    confidence = _tier(score)
    return Signal(direction, score, confidence, tuple(reasons), tuple(warnings))
