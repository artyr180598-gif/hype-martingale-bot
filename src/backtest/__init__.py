"""Бэктестер советника: walk-forward симуляция на реальной логике анализа."""

from src.backtest.engine import (
    BacktestConfig,
    Backtester,
    BacktestResult,
    Trade,
    closed_upto,
    resample_ohlcv,
    simulate_trade,
)
from src.backtest.metrics import compute_metrics
from src.backtest.report import backtest_report

__all__ = [
    "BacktestConfig",
    "Backtester",
    "BacktestResult",
    "Trade",
    "closed_upto",
    "resample_ohlcv",
    "simulate_trade",
    "compute_metrics",
    "backtest_report",
]
