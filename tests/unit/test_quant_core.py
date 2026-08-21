from app.backtesting.engine import Bar, run_fixed_bracket
from app.risk.engine import build_risk_plan
from app.signals.engine import Direction, SignalInput, score_signal


def test_risk_is_based_on_stop_distance() -> None:
    plan = build_risk_plan(1000, 100, 95, 0.01)
    assert round(plan.risk_amount, 8) == 10
    assert round(plan.quantity, 8) == 2
    assert round(plan.max_loss, 8) == 10


def test_degraded_data_suppresses_signal() -> None:
    values = SignalInput(1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, data_quality=0.69)
    signal = score_signal(values, Direction.LONG)
    assert signal.direction is Direction.NO_TRADE


def test_backtest_stop_wins_when_both_levels_are_touched() -> None:
    result = run_fixed_bracket(
        [Bar(1, 100, 110, 90, 105)],
        "LONG",
        entry=100,
        stop=95,
        target=108,
    )
    assert result.net_r == -1
