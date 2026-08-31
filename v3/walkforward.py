"""Walk-forward validation.

Instead of trusting one in-sample backtest, walk-forward splits history into
``TRAIN -> TEST`` folds.  Each fold is evaluated on data that was never used to
tune anything, and we report per-fold stability plus an aggregate risk-adjusted
summary.  This is a guard against overfitting: if one fold looks great and the
others are negative, the "edge" is not trusted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from v3.backtest import metrics_from_trades, run_backtest
from v3.config import SignalConfig
from v3.engine import FuturesSignalEngine


@dataclass
class WalkForwardConfig:
    train_bars: int = 600
    test_bars: int = 300
    step_bars: int = 300
    warmup_bars: int = 120
    n_folds: int = 5
    entry_tf: str = "15m"
    medium_tf: str = "1h"
    macro_tf: str = "4h"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class WalkFold:
    fold: int
    start_ts: int
    end_ts: int
    trades: int
    metrics: dict[str, Any]
    duration_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class WalkForwardResult:
    symbol: str
    config: WalkForwardConfig
    folds: list[WalkFold] = field(default_factory=list)
    aggregate: dict[str, Any] = field(default_factory=dict)
    stability: dict[str, Any] = field(default_factory=dict)
    is_demo: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "config": self.config.to_dict(),
            "folds": [f.to_dict() for f in self.folds],
            "aggregate": self.aggregate,
            "stability": self.stability,
            "is_demo": self.is_demo,
            "error": self.error,
        }


def walk_forward(
    engine: FuturesSignalEngine,
    symbol: str,
    history: pd.DataFrame,
    cfg: SignalConfig | None = None,
    wf: WalkForwardConfig | None = None,
) -> WalkForwardResult:
    cfg = cfg or SignalConfig()
    wf = wf or WalkForwardConfig()

    df = history.copy()
    for col in ("ts", "open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)

    needed = wf.warmup_bars + wf.train_bars + wf.test_bars * wf.n_folds
    if len(df) < needed:
        return WalkForwardResult(
            symbol=symbol.upper(),
            config=wf,
            is_demo=engine.data.is_demo,
            error=f"not enough history: {len(df)} bars, need >= {needed}",
        )

    folds: list[WalkFold] = []
    global_trades: list[Any] = []
    start_idx = wf.warmup_bars
    for fold in range(wf.n_folds):
        test_end = start_idx + wf.test_bars
        test_start = test_end - wf.test_bars
        # slicing includes the warm-up prefix so the engine has enough indicators
        sub = df.iloc[test_start - wf.warmup_bars : test_end].reset_index(drop=True)
        fold_started = time.time()
        res = run_backtest(
            engine, symbol, sub,
            entry_tf=wf.entry_tf,
            medium_tf=wf.medium_tf,
            macro_tf=wf.macro_tf,
            warmup=wf.warmup_bars,
            cfg=cfg,
        )
        global_trades.extend(res.trades)
        folds.append(
            WalkFold(
                fold=fold + 1,
                start_ts=int(sub["ts"].iloc[0]),
                end_ts=int(sub["ts"].iloc[-1]),
                trades=len(res.trades),
                metrics=res.metrics,
                duration_sec=round(time.time() - fold_started, 3),
            )
        )
        start_idx = test_end  # next fold overlays the prior test window

    aggregate = metrics_from_trades(global_trades)
    aggregate["fold_expectancies"] = [f.metrics.get("expectancy_r", 0.0) for f in folds]

    # stability: consistency of fold expectancy, not variance of winrate
    exps = np.array(aggregate["fold_expectancies"], dtype=float)
    pos_folds = int((exps > 0).sum())
    stability = {
        "fold_expectancy_mean": round(float(exps.mean()), 4),
        "fold_expectancy_std": round(float(exps.std()), 4),
        "positive_folds": pos_folds,
        "total_folds": len(folds),
        "worst_fold_expectancy": round(float(exps.min()), 4),
        "best_fold_expectancy": round(float(exps.max()), 4),
        "verdict": (
            "STABLE" if pos_folds >= max(1, len(folds) - 1) and float(exps.mean()) > 0
            else "UNSTABLE" if pos_folds <= max(1, len(folds) // 2)
            else "MIXED"
        ),
    }
    return WalkForwardResult(
        symbol=symbol.upper(),
        config=wf,
        folds=folds,
        aggregate=aggregate,
        stability=stability,
        is_demo=engine.data.is_demo,
    )
