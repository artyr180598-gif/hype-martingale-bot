"""
Strategy Conflict Resolution and Probabilistic Scenario Modeling.
"""
from src.config.constants import SignalDirection
from src.signals.models import ScenarioProbabilities
from src.strategies.base import StrategySignal


class ConflictResolver:
    """
    Evaluates strategy disagreement and generates calibrated scenario probabilities.
    """

    @classmethod
    def resolve_conflicts(
        cls,
        signals: list[StrategySignal],
        base_confidence: float = 0.80,
    ) -> tuple[bool, ScenarioProbabilities, list[str]]:
        """
        Analyze conflicting strategy signals and return scenario probability distribution.
        """
        if not signals:
            return False, ScenarioProbabilities(
                long_probability_pct=25.0,
                short_probability_pct=25.0,
                no_trade_probability_pct=50.0,
            ), ["No active strategy signals generated"]

        long_weights = sum(s.score * s.confidence for s in signals if s.direction == SignalDirection.LONG)
        short_weights = sum(s.score * s.confidence for s in signals if s.direction == SignalDirection.SHORT)
        total = long_weights + short_weights

        reasons: list[str] = []
        has_conflict = False

        if total == 0:
            return False, ScenarioProbabilities(
                long_probability_pct=15.0,
                short_probability_pct=15.0,
                no_trade_probability_pct=70.0,
            ), ["All strategies returned neutral stance"]

        raw_long_p = (long_weights / total) * 100.0
        raw_short_p = (short_weights / total) * 100.0

        # Conflict check: significant competing weight on both sides
        if long_weights > 0 and short_weights > 0:
            minor_ratio = min(long_weights, short_weights) / max(long_weights, short_weights)
            if minor_ratio > 0.35:
                has_conflict = True
                reasons.append("Models are in conflict: both LONG and SHORT signals actively triggered")

        # Uncertainty buffer (calibrated probability distribution)
        uncertainty_pct = 15.0 if not has_conflict else 35.0
        dist_scale = (100.0 - uncertainty_pct) / 100.0

        long_pct = round(raw_long_p * dist_scale, 1)
        short_pct = round(raw_short_p * dist_scale, 1)
        no_trade_pct = round(100.0 - (long_pct + short_pct), 1)

        return has_conflict, ScenarioProbabilities(
            long_probability_pct=long_pct,
            short_probability_pct=short_pct,
            no_trade_probability_pct=no_trade_pct,
        ), reasons
