"""
No-Trade Filter and Signal Suppression Engine.
"""
from src.config.constants import DataQualityStatus
from src.data.models import DataQualityReport
from src.signals.models import SignalSetup


class NoTradeEngine:
    """
    Enforces quality gates before any setup can be broadcast or executed.
    'NO TRADE is a valid and often preferable output.'
    """

    @classmethod
    def should_suppress_signal(
        cls,
        setup: SignalSetup,
        data_quality: DataQualityReport | None = None,
        has_model_conflict: bool = False,
        macro_counter_trend: bool = False,
    ) -> tuple[bool, list[str]]:
        suppress_reasons: list[str] = []

        # 1. Data Quality Gate
        if data_quality and (not data_quality.is_acceptable_for_trading or data_quality.status == DataQualityStatus.DEGRADED):
            suppress_reasons.append(f"Data quality degraded (Score: {data_quality.quality_score:.2f}) — trading suppressed for capital protection")

        # 2. Score Gate (< 60 is NO_TRADE)
        if setup.score < 60.0:
            suppress_reasons.append(f"Signal score ({setup.score:.1f}/100) below minimum validity threshold (60.0)")

        # 3. Model Disagreement Gate
        if has_model_conflict:
            suppress_reasons.append("Strategy ensemble conflict detected between trend and mean-reversion engines")

        # 4. Risk / Reward Gate (< 1:1.5)
        if setup.risk_reward_ratio < 1.5:
            suppress_reasons.append(f"Unfavorable Risk/Reward ratio (1:{setup.risk_reward_ratio:.1f} < 1:1.5 minimum required)")

        # 5. Counter Trend Gate
        if macro_counter_trend and setup.score < 80.0:
            suppress_reasons.append("Counter-trend trade against Macro 4H direction lacking extreme conviction score (>= 80.0)")

        should_suppress = len(suppress_reasons) > 0
        return should_suppress, suppress_reasons
