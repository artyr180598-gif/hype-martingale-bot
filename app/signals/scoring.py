from dataclasses import dataclass
from enum import StrEnum


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"
    WATCH = "WATCH"


@dataclass(frozen=True, slots=True)
class SignalDecision:
    direction: Direction
    score: float
    confidence: float
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


def classify_score(score: float) -> str:
    if score >= 90:
        return "EXTREME_SETUP"
    if score >= 80:
        return "STRONG_SETUP"
    if score >= 70:
        return "VALID_SETUP"
    if score >= 60:
        return "WATCH"
    return "NO_TRADE"


def suppress_signal(
    direction: Direction,
    score: float,
    *,
    data_quality: float,
    spread_normal: bool = True,
    sufficient_liquidity: bool = True,
    expected_rr: float | None = None,
    historical_expectancy: float | None = None,
    model_disagreement: float = 0.0,
) -> SignalDecision:
    reasons: list[str] = []
    warnings: list[str] = []
    if data_quality < 90:
        reasons.append("degraded_data_quality")
    if not spread_normal:
        reasons.append("abnormal_spread")
    if not sufficient_liquidity:
        reasons.append("insufficient_liquidity")
    if expected_rr is not None and expected_rr < 1.5:
        reasons.append("poor_risk_reward")
    if historical_expectancy is not None and historical_expectancy <= 0:
        reasons.append("non_positive_historical_expectancy")
    if model_disagreement >= 0.50:
        reasons.append("high_model_disagreement")
    if score < 60:
        reasons.append("score_below_trade_threshold")
    if direction is Direction.NO_TRADE:
        reasons.append("strategy_did_not_produce_trade_direction")
    if reasons:
        return SignalDecision(Direction.NO_TRADE, score, max(0.0, min(score, data_quality)), tuple(reasons), tuple(warnings))
    return SignalDecision(direction, score, max(0.0, min(score, data_quality)), tuple(reasons), tuple(warnings))
