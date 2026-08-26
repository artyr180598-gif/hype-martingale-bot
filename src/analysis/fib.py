"""Уровни Фибоначчи: ретрейсменты, расширения, зоны входа/выхода, R:R."""

from __future__ import annotations

from dataclasses import dataclass, field

RETRACEMENTS = [0.236, 0.382, 0.5, 0.618, 0.786]
EXTENSIONS = [1.0, 1.272, 1.618, 2.0, 2.618]


@dataclass
class FibLevels:
    base_low: float
    base_high: float
    direction: int  # 1 — тренд вверх (ретрейсмент от низа к верху), -1 — вниз
    retracements: dict[float, float] = field(default_factory=dict)
    extensions: dict[float, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        span = self.base_high - self.base_low
        self.retracements = {
            r: self.base_high - span * r if self.direction > 0 else self.base_low + span * r
            for r in RETRACEMENTS
        }
        self.extensions = {
            e: self.base_low + span * e if self.direction > 0 else self.base_high - span * e
            for e in EXTENSIONS
        }


def fib_levels(base_low: float, base_high: float, direction: int = 1) -> FibLevels:
    """Уровни фибо для свинга [base_low..base_high]."""
    return FibLevels(base_low=base_low, base_high=base_high, direction=direction)


def compute_rr(
    entry: float,
    stop: float,
    target: float,
    direction: int,
) -> float:
    """Риск/прибыль для позиции в направлении direction."""
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0:
        return 0.0
    return reward / risk


def best_rr(
    entry: float,
    stop: float,
    targets: list[float],
    direction: int,
) -> tuple[float, float]:
    """Лучшее R:R среди целей. Возвращает (R:R, целевая цена)."""
    best, best_price = 0.0, 0.0
    for t in targets:
        rr = compute_rr(entry, stop, t, direction)
        if rr > best:
            best, best_price = rr, t
    return best, best_price
