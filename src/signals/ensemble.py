"""
Quantitative Multi-Strategy Ensemble Engine.
"""
from typing import Any

from src.config.constants import SignalDirection
from src.core.logging import get_logger
from src.regime.classifier import MarketRegimeClassifier
from src.strategies.base import StrategySignal
from src.strategies.registry import StrategyRegistry

logger = get_logger("signals.ensemble")


class StrategyEnsembleEngine:
    """
    Synthesizes signals from independent quantitative strategies weighted by regime suitability.
    """

    @classmethod
    def evaluate_ensemble(cls, features: dict[str, Any]) -> tuple[StrategySignal | None, list[StrategySignal]]:
        """
        Run all registered strategies and select the highest-conviction coherent signal.
        """
        active_strategies = StrategyRegistry.list_active()
        if not active_strategies or not features:
            return None, []

        regime_report = MarketRegimeClassifier.classify(features)
        cur_regime = regime_report.regime

        signals: list[StrategySignal] = []
        long_score_weight = 0.0
        short_score_weight = 0.0
        total_weight = 0.0

        for strat in active_strategies:
            try:
                sig = strat.evaluate(features)
                if sig.direction != SignalDirection.NO_TRADE:
                    # Weight by regime match
                    regime_mult = 1.35 if cur_regime in strat.expected_regimes else 0.80
                    effective_weight = sig.confidence * regime_mult

                    if sig.direction == SignalDirection.LONG:
                        long_score_weight += sig.score * effective_weight
                    elif sig.direction == SignalDirection.SHORT:
                        short_score_weight += sig.score * effective_weight

                    total_weight += effective_weight
                    signals.append(sig)
            except Exception as e:
                logger.error("Strategy evaluation error", strategy=strat.name, error=str(e))

        if not signals:
            return None, []

        # Sort signals by score * confidence
        signals.sort(key=lambda s: s.score * s.confidence, reverse=True)
        top_signal = signals[0]

        return top_signal, signals
