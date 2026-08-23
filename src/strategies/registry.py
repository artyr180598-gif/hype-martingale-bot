"""
Strategy Registry and Model Lifecycle Management.
"""

from src.config.constants import StrategyStatus
from src.core.logging import get_logger
from src.strategies.base import BaseStrategy

logger = get_logger("strategies.registry")


class StrategyRegistry:
    """
    Central repository of quantitative trading models with status gates.
    """

    _strategies: dict[str, BaseStrategy] = {}

    @classmethod
    def register(cls, strategy: BaseStrategy) -> None:
        cls._strategies[strategy.name] = strategy
        logger.info(
            "Registered strategy",
            name=strategy.name,
            version=strategy.version,
            status=strategy.status.value,
        )

    @classmethod
    def get(cls, name: str) -> BaseStrategy | None:
        return cls._strategies.get(name)

    @classmethod
    def list_all(cls) -> list[BaseStrategy]:
        return list(cls._strategies.values())

    @classmethod
    def list_active(cls) -> list[BaseStrategy]:
        """Return only PRODUCTION and CANDIDATE strategies for ensemble evaluation."""
        return [
            s
            for s in cls._strategies.values()
            if s.status in (StrategyStatus.PRODUCTION, StrategyStatus.CANDIDATE)
        ]
