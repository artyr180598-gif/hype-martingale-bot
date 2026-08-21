from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StrategyContext:
    symbol: str
    timeframe: str
    features: dict[str, float]
    regime: str


@dataclass(frozen=True, slots=True)
class StrategySignal:
    strategy: str
    version: str
    direction: str
    score: float
    rationale: tuple[str, ...]


class Strategy(Protocol):
    name: str
    version: str

    def evaluate(self, context: StrategyContext) -> StrategySignal: ...


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        if strategy.name in self._strategies:
            raise ValueError(f"strategy_already_registered:{strategy.name}")
        self._strategies[strategy.name] = strategy

    def get(self, name: str) -> Strategy:
        return self._strategies[name]

    def all(self) -> tuple[Strategy, ...]:
        return tuple(self._strategies.values())
