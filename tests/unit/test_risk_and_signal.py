from app.risk.sizing import size_from_stop
from app.signals.scoring import Direction, classify_score, suppress_signal


def test_position_size_is_derived_from_stop() -> None:
    sized = size_from_stop(10_000, 100, 95, 0.01)
    assert sized.risk_amount == 100
    assert sized.stop_distance == 5
    assert sized.quantity == 20
    assert sized.notional == 2_000


def test_score_tier() -> None:
    assert classify_score(87) == "STRONG_SETUP"
    assert classify_score(59) == "NO_TRADE"


def test_signal_suppressed_when_data_is_degraded() -> None:
    decision = suppress_signal(Direction.LONG, 88, data_quality=72)
    assert decision.direction is Direction.NO_TRADE
    assert "degraded_data_quality" in decision.reasons
