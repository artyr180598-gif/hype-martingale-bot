from app.backtesting.metrics import calculate_metrics
from app.monte_carlo.engine import simulate
from app.validation.walk_forward import walk_forward_windows


def test_metrics_count_and_drawdown() -> None:
    metrics = calculate_metrics([1.0, -0.5, 2.0, -1.0])
    assert metrics.trades == 4
    assert metrics.net_r == 1.5
    assert metrics.win_rate == 0.5
    assert metrics.max_drawdown_r == -1.0


def test_walk_forward_is_chronological_and_disjoint() -> None:
    windows = walk_forward_windows(list(range(20)), 10, 5, 5)
    assert len(windows) == 1
    assert max(windows[0].train) < min(windows[0].validation)
    assert max(windows[0].validation) < min(windows[0].test)


def test_monte_carlo_preserves_final_sum() -> None:
    summary = simulate([1.0, -0.5, 0.25], simulations=100, seed=7)
    assert summary.median_final_r == 0.75
    assert summary.p05_final_r == 0.75
    assert summary.p95_final_r == 0.75
