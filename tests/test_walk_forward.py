"""
Tests for Walk Forward Validation Engine.
"""
import numpy as np

from src.backtesting.walk_forward import WalkForwardValidator
from src.core.time_utils import utc_now_ms
from src.data.models import CandleData
from src.strategies.trend_following import TrendFollowingStrategy


def test_walk_forward_execution():
    candles = []
    base_price = 50000.0
    base_ts = utc_now_ms() - (300 * 15 * 60 * 1000)
    current_p = base_price

    np.random.seed(123)
    for i in range(300):
        ts = base_ts + (i * 15 * 60 * 1000)
        step = np.random.normal(20.0, 40.0)
        open_p = current_p
        close_p = open_p + step
        high_p = max(open_p, close_p) + 15.0
        low_p = min(open_p, close_p) - 15.0
        vol = 50.0

        candles.append(
            CandleData(
                symbol="BTCUSDT",
                timeframe="15m",
                timestamp_ms=ts,
                open=round(open_p, 2),
                high=round(high_p, 2),
                low=round(low_p, 2),
                close=round(close_p, 2),
                volume=round(vol, 4),
            )
        )
        current_p = close_p

    strat = TrendFollowingStrategy()
    wf = WalkForwardValidator(strategy=strat, num_folds=3, train_ratio=0.70)
    report = wf.run_walk_forward(candles)

    assert report.total_folds == 3
    assert len(report.folds) == 3
    assert report.strategy_name == strat.name
