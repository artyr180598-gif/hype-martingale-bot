"""Backtesting package."""
from src.backtesting.baselines import BenchmarkEvaluator
from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.metrics import BacktestMetrics, MetricsCalculator
from src.backtesting.monte_carlo import MonteCarloReport, MonteCarloSimulator
from src.backtesting.regime_evaluator import RegimePerformanceEvaluator
from src.backtesting.walk_forward import (
    WalkForwardFoldResult,
    WalkForwardReport,
    WalkForwardValidator,
)

__all__ = [
    "BacktestEngine",
    "BacktestMetrics",
    "BacktestResult",
    "BenchmarkEvaluator",
    "MetricsCalculator",
    "MonteCarloReport",
    "MonteCarloSimulator",
    "RegimePerformanceEvaluator",
    "WalkForwardFoldResult",
    "WalkForwardReport",
    "WalkForwardValidator",
]
