"""Тесты волнового анализа, волатильности и структуры."""

import numpy as np
import pandas as pd

from src.analysis.waves import (
    elliott,
    market_structure,
    momentum,
    volatility_state,
    zigzag,
)
from src.data.indicators import compute_all


def make_trend_df(n: int = 400, trend: str = "up", seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = 0.002 if trend == "up" else -0.002
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.008, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = rng.uniform(50, 200, n)
    ts = pd.Series(np.arange(n) * 900_000)
    return pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_zigzag_alternates():
    df = compute_all(make_trend_df(n=500))
    zz = zigzag(df, pct_threshold=0.5, use_atr=True)
    assert len(zz) >= 4
    diffs = [zz[i + 1][1] - zz[i][1] for i in range(len(zz) - 1)]
    assert all(d != 0 for d in diffs)
    # чередование знаков
    signs = [1 if d > 0 else -1 for d in diffs]
    for i in range(len(signs) - 1):
        assert signs[i] != signs[i + 1], f"не чередуется на {i}: {zz[i:i+3]}"


def test_elliott_on_trend():
    df = compute_all(make_trend_df(n=600, trend="up"))
    res = elliott(df)
    assert res.pattern in {"impulse", "correction", "unclear"}
    assert res.wave_position in {0, 3, 5}
    assert res.trend_dir in {-1, 1}
    assert res.confidence > 0


def test_volatility_state():
    df = compute_all(make_trend_df(n=300))
    vs = volatility_state(df)
    assert vs.state in {"low", "normal", "high", "extreme", "squeeze"}
    assert vs.atr_pct > 0
    assert 0.0 <= vs.atr_pctl <= 1.0


def test_market_structure_up_trend():
    df = compute_all(make_trend_df(n=400, trend="up", seed=5))
    st = market_structure(df)
    assert st.trend in {"up", "down", "range"}
    assert st.support is not None and st.resistance is not None
    assert st.strength >= 0


def test_momentum_fields():
    df = compute_all(make_trend_df(n=300))
    m = momentum(df)
    assert 0 <= m.rsi <= 100
    assert m.st_dir in {-1, 1}
    assert isinstance(m.macd_hist, float)
