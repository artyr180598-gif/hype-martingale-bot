from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BaselineSignal:
    direction: str
    score: float
    reason: str


def momentum_baseline(return_pct: float, threshold_pct: float = 0.25) -> BaselineSignal:
    if return_pct > threshold_pct:
        return BaselineSignal("LONG", 50.0, "positive_momentum")
    if return_pct < -threshold_pct:
        return BaselineSignal("SHORT", 50.0, "negative_momentum")
    return BaselineSignal("NO_TRADE", 0.0, "insufficient_momentum")


def random_baseline() -> str:
    """Name-only baseline marker; randomness must never enter production signals."""
    return "RANDOM_ENTRY_BASELINE"
