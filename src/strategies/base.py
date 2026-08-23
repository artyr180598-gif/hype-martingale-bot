"""
Abstract Base Quantitative Strategy and Strategy Signal Model.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.config.constants import (
    EntryType,
    MarketRegimeType,
    SignalDirection,
    StrategyStatus,
)


@dataclass
class StrategySignal:
    strategy_name: str
    strategy_version: str
    symbol: str
    timeframe: str
    timestamp_ms: int
    direction: SignalDirection
    score: float             # 0.0 to 100.0
    confidence: float        # 0.0 to 1.0
    entry_type: EntryType
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float | None
    take_profit_3: float | None
    risk_reward_ratio: float
    invalidation: str
    reasons: list[str]
    risk_warnings: list[str]


class BaseStrategy(ABC):
    """
    Base contract for all deterministic quantitative strategy models.
    """

    def __init__(
        self,
        name: str,
        version: str = "1.0.0",
        status: StrategyStatus = StrategyStatus.PRODUCTION,
        expected_regimes: list[MarketRegimeType] | None = None,
    ):
        self.name = name
        self.version = version
        self.status = status
        self.expected_regimes = expected_regimes or []

    @abstractmethod
    def evaluate(self, features: dict[str, Any]) -> StrategySignal:
        """
        Evaluate feature matrix and generate a deterministic strategy hypothesis.
        """
