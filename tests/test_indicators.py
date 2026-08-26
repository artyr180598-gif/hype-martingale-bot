"""Тесты технических индикаторов."""

import numpy as np
import pandas as pd
import pytest

from src.data.indicators import (
    adx,
    atr,
    bollinger,
    compute_all,
    ema,
    macd,
    rsi,
    stochastic,
    supertrend,
    volume_zscore,
)


def make_df(n: int = 200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = rng.uniform(50, 200, n)
    ts = pd.Series(np.arange(n) * 900_000)
    return pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_rsi_bounds():
    df = make_df()
    r = rsi(df["close"], 14)
    assert r.dropna().between(0, 100).all()
    assert len(r) == len(df)


def test_atr_positive():
    df = make_df()
    a = atr(df["high"], df["low"], df["close"], 14)
    assert (a.dropna() > 0).all()


def test_bollinger_bands_envelope():
    df = make_df()
    bb = bollinger(df["close"], 20, 2.0)
    valid = bb.dropna()
    assert (valid["bb_up"] >= valid["bb_low"]).all()
    assert valid["bb_pctb"].between(-1.5, 2.5).all()


def test_adx_range():
    df = make_df()
    a = adx(df["high"], df["low"], df["close"])
    assert a["adx"].dropna().between(0, 100).all()
    assert (a["plus_di"].dropna() >= 0).all()


def test_macd_signals():
    df = make_df()
    m = macd(df["close"])
    assert len(m) == len(df)
    assert (m["macd_hist"] == m["macd"] - m["macd_signal"]).all()


def test_stochastic_range():
    df = make_df()
    s = stochastic(df["high"], df["low"], df["close"])
    assert s["stoch_k"].dropna().between(0, 100).all()


def test_supertrend_output():
    df = make_df()
    st = supertrend(df["high"], df["low"], df["close"])
    assert set(st["st_dir"].unique()).issubset({-1, 1})
    assert st["st_trend"].notna().sum() > len(df) * 0.9


def test_volume_zscore():
    df = make_df()
    z = volume_zscore(df["volume"], 20)
    assert z.dropna().abs().mean() < 5


def test_compute_all_columns():
    df = make_df()
    out = compute_all(df)
    required = [
        "ema_20", "ema_50", "rsi_14", "mfi_14", "atr_14", "atr_pct",
        "bb_up", "bb_low", "bb_width", "kc_up", "kc_low", "adx", "macd_hist",
        "stoch_k", "st_trend", "st_dir", "obv", "cvd", "vwap", "vol_z", "rv_20",
    ]
    for col in required:
        assert col in out.columns, col
    assert out["atr_pct"].dropna().between(0, 100).all()
