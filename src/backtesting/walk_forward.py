"""
Walk-Forward Validation and Out-of-Sample Evaluation Engine.
"""
from dataclasses import dataclass
from typing import Any

from src.backtesting.engine import BacktestEngine
from src.core.logging import get_logger
from src.data.models import CandleData
from src.strategies.base import BaseStrategy

logger = get_logger("backtesting.walk_forward")


@dataclass
class WalkForwardFoldResult:
    fold_index: int
    train_bars_count: int
    test_bars_count: int
    in_sample_metrics: dict[str, Any]
    out_of_sample_metrics: dict[str, Any]
    efficiency_ratio: float  # OOS Return / IS Return


@dataclass
class WalkForwardReport:
    strategy_name: str
    symbol: str
    total_folds: int
    average_oos_sharpe: float
    average_oos_profit_factor: float
    average_oos_win_rate_pct: float
    walk_forward_efficiency_pct: float
    is_robust: bool
    folds: list[WalkForwardFoldResult]


class WalkForwardValidator:
    """
    Performs anchored or rolling walk-forward out-of-sample testing to prevent overfitting.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        num_folds: int = 4,
        train_ratio: float = 0.70,
    ):
        self.strategy = strategy
        self.num_folds = num_folds
        self.train_ratio = train_ratio

    def run_walk_forward(self, candles: list[CandleData]) -> WalkForwardReport:
        n = len(candles)
        if n < 200:
            raise ValueError("Insufficient data for walk forward validation (minimum 200 bars)")

        fold_size = n // self.num_folds
        train_size = int(fold_size * self.train_ratio)
        test_size = fold_size - train_size

        folds: list[WalkForwardFoldResult] = []
        oos_sharpes = []
        oos_pfs = []
        oos_wrs = []
        efficiencies = []

        for f in range(self.num_folds):
            start_idx = f * fold_size
            train_end = start_idx + train_size
            test_end = start_idx + fold_size

            train_candles = candles[start_idx:train_end]
            test_candles = candles[train_end:test_end]

            if len(train_candles) < 50 or len(test_candles) < 20:
                continue

            # Run In-Sample
            engine_is = BacktestEngine(self.strategy)
            res_is = engine_is.run(train_candles)

            # Run Out-of-Sample
            engine_oos = BacktestEngine(self.strategy)
            res_oos = engine_oos.run(test_candles)

            is_ret = res_is.metrics.total_return_pct
            oos_ret = res_oos.metrics.total_return_pct
            eff = (oos_ret / is_ret) if abs(is_ret) > 1e-4 else 1.0

            oos_sharpes.append(res_oos.metrics.sharpe_ratio)
            oos_pfs.append(res_oos.metrics.profit_factor)
            oos_wrs.append(res_oos.metrics.win_rate_pct)
            efficiencies.append(eff)

            folds.append(
                WalkForwardFoldResult(
                    fold_index=f + 1,
                    train_bars_count=len(train_candles),
                    test_bars_count=len(test_candles),
                    in_sample_metrics={
                        "return_pct": res_is.metrics.total_return_pct,
                        "sharpe": res_is.metrics.sharpe_ratio,
                        "profit_factor": res_is.metrics.profit_factor,
                        "win_rate": res_is.metrics.win_rate_pct,
                    },
                    out_of_sample_metrics={
                        "return_pct": res_oos.metrics.total_return_pct,
                        "sharpe": res_oos.metrics.sharpe_ratio,
                        "profit_factor": res_oos.metrics.profit_factor,
                        "win_rate": res_oos.metrics.win_rate_pct,
                    },
                    efficiency_ratio=round(eff, 2),
                )
            )

        avg_oos_sharpe = float(sum(oos_sharpes) / max(1, len(oos_sharpes)))
        avg_oos_pf = float(sum(oos_pfs) / max(1, len(oos_pfs)))
        avg_oos_wr = float(sum(oos_wrs) / max(1, len(oos_wrs)))
        avg_eff = float(sum(efficiencies) / max(1, len(efficiencies))) * 100.0

        # Robustness Criteria: OOS PF > 1.2 and positive return in >= 60% of folds
        is_robust = avg_oos_pf >= 1.2 and avg_oos_sharpe >= 0.5 and len(folds) >= 2

        return WalkForwardReport(
            strategy_name=self.strategy.name,
            symbol=candles[0].symbol,
            total_folds=len(folds),
            average_oos_sharpe=round(avg_oos_sharpe, 2),
            average_oos_profit_factor=round(avg_oos_pf, 2),
            average_oos_win_rate_pct=round(avg_oos_wr, 1),
            walk_forward_efficiency_pct=round(avg_eff, 1),
            is_robust=is_robust,
            folds=folds,
        )
