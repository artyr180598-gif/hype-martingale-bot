from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    name: str
    description: str
    source: str
    timeframe: str
    lookback: int
    formula: str
    availability: str = "close"


class FeatureRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, FeatureDefinition] = {}
        self._calculators: dict[str, Callable[..., float | None]] = {}

    def register(
        self, definition: FeatureDefinition, calculator: Callable[..., float | None]
    ) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"feature_already_registered:{definition.name}")
        self._definitions[definition.name] = definition
        self._calculators[definition.name] = calculator

    def definition(self, name: str) -> FeatureDefinition:
        return self._definitions[name]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def calculate(self, name: str, *args: object, **kwargs: object) -> float | None:
        return self._calculators[name](*args, **kwargs)
