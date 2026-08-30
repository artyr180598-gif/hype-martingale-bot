"""
Индикаторы v2: корректность формул.

Главная проверка — кросс-сверка с эталонной pandas-реализацией из v1
(src/data/indicators.py). Если numpy-код разойдётся с классикой Wilder,
тест это поймает, а вместе с ним поедут и стопы (они считаются от ATR).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# эталон из v1 — отдельный пакет, импортируем напрямую
from src.data import indicators as ref
from v2.analysis import indicators as ind


def _series(n: int = 300, seed: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.8, n))
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    volume = np.abs(rng.normal(1000, 300, n)) + 50
    return high, low, close, volume


def test_atr_matches_pandas_reference():
    high, low, close, _ = _series()
    mine = ind.atr(high, low, close, 14)
    theirs = ref.atr(pd.Series(high), pd.Series(low), pd.Series(close), 14)
    assert mine[-1] == pytest.approx(float(theirs.iloc[-1]), rel=1e-4)


def test_rsi_matches_pandas_reference():
    _, _, close, _ = _series()
    mine = ind.rsi(close, 14)
    theirs = ref.rsi(pd.Series(close), 14)
    assert mine[-1] == pytest.approx(float(theirs.iloc[-1]), abs=0.5)


def test_adx_matches_pandas_reference():
    high, low, close, _ = _series()
    mine = ind.adx(high, low, close, 14)
    theirs = ref.adx(pd.Series(high), pd.Series(low), pd.Series(close), 14)
    assert mine["adx"][-1] == pytest.approx(float(theirs["adx"].iloc[-1]), abs=1.0)
    assert mine["plus_di"][-1] == pytest.approx(float(theirs["plus_di"].iloc[-1]), abs=1.0)


def test_obv_matches_pandas_reference():
    _, _, close, volume = _series()
    mine = ind.obv(close, volume)
    theirs = ref.obv(pd.Series(close), pd.Series(volume))
    assert mine[-1] == pytest.approx(float(theirs.iloc[-1]), rel=1e-9)


def test_ema_matches_pandas_reference():
    _, _, close, _ = _series()
    mine = ind.ema(close, 20)
    theirs = ref.ema(pd.Series(close), 20)
    assert mine[-1] == pytest.approx(float(theirs.iloc[-1]), rel=1e-6)


def test_atr_positive_and_scaled_to_price():
    high, low, close, _ = _series()
    atr_value = ind.last(ind.atr(high, low, close, 14))
    assert atr_value > 0
    pct = ind.last(ind.atr_pct(high, low, close, 14))
    assert 0 < pct < 20  # разумный диапазон для синтетики


def test_rsi_bounds():
    _, _, close, _ = _series()
    values = ind.rsi(close, 14)
    finite = values[np.isfinite(values)]
    assert finite.min() >= 0.0 and finite.max() <= 100.0


def test_adx_flat_market_is_low():
    """В шуме без тренда ADX должен быть низким — иначе фильтр тренда врёт."""
    rng = np.random.default_rng(3)
    n = 250
    close = 100 + rng.normal(0, 0.2, n).cumsum() * 0.05
    high = close + 0.05
    low = close - 0.05
    adx_value = ind.last(ind.adx(high, low, close, 14)["adx"])
    assert adx_value < 40


def test_trend_direction_rules():
    assert ind.trend_direction(30, 10, 35) == "up"
    assert ind.trend_direction(10, 30, 35) == "down"
    assert ind.trend_direction(30, 28, 35) == "flat"   # DI почти равны
    assert ind.trend_direction(30, 10, 12) == "flat"   # ADX ниже порога
    assert ind.trend_strength(45) == "strong"
    assert ind.trend_strength(30) == "moderate"
    assert ind.trend_strength(22) == "weak"
    assert ind.trend_strength(10) == "none"


def test_fib_levels_are_between_swing_points():
    levels = ind.fib_levels(100.0, 200.0, direction=1)
    retr = levels["retracements"]
    assert set(retr) == set(ind.RETRACEMENTS)
    assert retr[0.236] == pytest.approx(176.4)  # от вершины вниз
    assert retr[0.5] == pytest.approx(150.0)
    assert retr[0.618] == pytest.approx(138.2, abs=0.1)
    assert levels["extensions"][1.0] == pytest.approx(200.0)
    assert levels["extensions"][1.618] == pytest.approx(261.8, abs=0.1)


def test_fib_mirrored_for_downtrend():
    levels = ind.fib_levels(100.0, 200.0, direction=-1)
    assert levels["retracements"][0.5] == pytest.approx(150.0)
    assert levels["retracements"][0.618] == pytest.approx(161.8, abs=0.1)


def test_swing_points_find_extremes():
    high = np.array([1, 2, 3, 4, 5, 4, 3, 2, 1, 2, 3], dtype=float)
    low = high - 0.5
    highs, lows = ind.swing_points(high, low, left=2, right=2)
    assert 4 in highs            # пик на индексе 4
    assert ind.last_swing(high, low, direction=1, left=2, right=2) == pytest.approx(5.0)


def test_compute_rr():
    assert ind.compute_rr(100.0, 95.0, 110.0) == pytest.approx(2.0)
    assert ind.compute_rr(100.0, 100.0, 110.0) == 0.0   # нулевой риск → 0, а не деление на ноль


def test_obv_slope_detects_accumulation():
    """Растущая цена с растущим объёмом → положительный наклон OBV."""
    n = 60
    close = np.linspace(100, 120, n)
    volume = np.linspace(500, 1500, n)
    slope = ind.obv_slope(ind.obv(close, volume), 20)
    assert slope > 0.1

    falling = np.linspace(120, 100, n)
    slope_down = ind.obv_slope(ind.obv(falling, volume), 20)
    assert slope_down < -0.1


def test_volume_zscore_flags_spike():
    volume = np.full(50, 100.0)
    volume[-1] = 500.0
    z = ind.volume_zscore(volume, 20)
    assert z[-1] > 2.0


def test_vwap_between_low_and_high():
    high = np.array([11.0, 12.0, 13.0])
    low = np.array([9.0, 10.0, 11.0])
    close = np.array([10.0, 11.0, 12.0])
    volume = np.array([100.0, 100.0, 100.0])
    value = ind.last(ind.vwap(high, low, close, volume))
    assert low.min() <= value <= high.max()
