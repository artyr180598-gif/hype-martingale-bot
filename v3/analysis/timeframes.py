"""Multi-timeframe analysis.

Builds a ``TimeframeView`` for every configured timeframe using the same
indicator library as the live v1 engine.  Price/indicator values at bar *i*
depend only on bars ``<= i`` so this module is safe to call from the backtester.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from src.analysis.waves import market_structure, momentum, volatility_state
from src.data.indicators import compute_all
from v3.models import TimeframeView


def _safe(value: float, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if np.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _ema_stack(fe: pd.DataFrame) -> int:
    """−3..+3: сколько пар EMA ориентированы вверх (ema9>ema20, ema20>ema50, ema50>ema200)."""
    last = fe.iloc[-1]
    stack = 0
    for fast, slow in (("ema_9", "ema_20"), ("ema_20", "ema_50"), ("ema_50", "ema_200")):
        f, s = _safe(last.get(fast)), _safe(last.get(slow))
        if f and s:
            stack += 1 if f > s else -1
    return stack


def _macd_cross(fe: pd.DataFrame) -> int:
    """1 свежий бычий кросс MACD, −1 медвежий, 0 нет."""
    if len(fe) < 2:
        return 0
    prev, last = fe.iloc[-2], fe.iloc[-1]
    p_macd, p_sig = _safe(prev.get("macd")), _safe(prev.get("macd_signal"))
    l_macd, l_sig = _safe(last.get("macd")), _safe(last.get("macd_signal"))
    if p_macd <= p_sig and l_macd > l_sig:
        return 1
    if p_macd >= p_sig and l_macd < l_sig:
        return -1
    return 0


def _stoch_cross(fe: pd.DataFrame) -> int:
    """1 бычий кросс %K/%D, −1 медвежий, 0 нет."""
    if len(fe) < 2:
        return 0
    prev, last = fe.iloc[-2], fe.iloc[-1]
    pk, pd_ = _safe(prev.get("stoch_k"), 50.0), _safe(prev.get("stoch_d"), 50.0)
    lk, ld = _safe(last.get("stoch_k"), 50.0), _safe(last.get("stoch_d"), 50.0)
    if pk <= pd_ and lk > ld:
        return 1
    if pk >= pd_ and lk < ld:
        return -1
    return 0


def _rvol(fe: pd.DataFrame, window: int = 20) -> float:
    """Относительный объём: последний бар / среднее за окно (без будущего)."""
    try:
        vol = pd.to_numeric(fe["volume"], errors="coerce").dropna()
        if len(vol) < max(5, window // 2):
            return 1.0
        # Не включаем текущую свечу в baseline: её объём может быть первым
        # признаком импульса и не должен разбавлять сам себя.
        avg = float(vol.iloc[:-1].tail(window).mean())
        return float(vol.iloc[-1] / avg) if avg > 0 else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def _structure_signal(fe: pd.DataFrame, structure: Any) -> str:
    """BOS/CHoCH с учётом тренда (исправление раунда 4).

    Раньше «любой новый максимум» помечался BOS_UP, а CHoCH выпадал случайно.
    Теперь: BOS = пробитие экстремума ПО ходу тренда; CHoCH = первый
    противо-трендовый пробой (смена характера); в диапазоне новый экстремум
    — это и есть смена характера.
    """
    close = _safe(fe["close"].iloc[-1]) if len(fe) else 0.0
    if close <= 0:
        return "none"
    sw_highs = [p for p in structure_resistance_series(fe) if p is not None]
    sw_lows = [p for p in structure_support_series(fe) if p is not None]
    if len(sw_highs) < 2 or len(sw_lows) < 2:
        return "none"
    if not floats_finite(sw_highs[-2:]) or not floats_finite(sw_lows[-2:]):
        return "none"
    hh = sw_highs[-1] > sw_highs[-2]
    hl = sw_lows[-1] > sw_lows[-2]
    lh = sw_highs[-1] < sw_highs[-2]
    ll = sw_lows[-1] < sw_lows[-2]

    if structure.trend == "up":
        if hh or hl:
            return "BOS_UP"          # продолжение: структура делает HH/HL
        if lh or ll:
            return "CHoCH_DOWN"      # первый разворотный пробой вверх-тренде
    elif structure.trend == "down":
        if ll or lh:
            return "BOS_DOWN"
        if hh or hl:
            return "CHoCH_UP"
    else:
        # диапазон/неопределённость: новый экстремум = смена характера
        if hh and not lh:
            return "CHoCH_UP"
        if ll and not hl:
            return "CHoCH_DOWN"
    return "none"


def build_timeframe_view(df: pd.DataFrame, timeframe: str) -> TimeframeView:
    fe = compute_all(df)
    last = fe.iloc[-1]
    structure = market_structure(fe)
    vol = volatility_state(fe)
    mom = momentum(fe)
    close = _safe(last["close"])
    price = close or float(df["close"].iloc[-1])

    vwap = _safe(last["vwap"], price)
    vwap_dist = (price - vwap) / price * 100.0 if price else 0.0

    structure_signal = _structure_signal(fe, structure)

    atr_pct = _safe(last.get("atr_pct"), 0.0)
    cvd_trend = mom.cvd_trend
    obv_trend = mom.obv_trend

    return TimeframeView(
        timeframe=timeframe,
        trend=structure.trend,
        adx=round(_safe(structure.adx), 2),
        rsi=round(_safe(mom.rsi, 50), 1),
        macd_hist=round(_safe(mom.macd_hist), 6),
        stoch_k=round(_safe(mom.stoch_k, 50), 1),
        atr=round(_safe(last.get("atr_14")), 8),
        atr_pct=round(atr_pct, 4),
        atr_pctl=round(_safe(vol.atr_pctl), 4),
        vol_z=round(_safe(mom.vol_z), 3),
        cvd_trend=round(cvd_trend, 3),
        obv_trend=round(obv_trend, 3),
        squeeze=bool(vol.squeeze),
        supertrend=1 if mom.st_dir > 0 else -1,
        vwap_dist_pct=round(vwap_dist, 4),
        support=float(structure.support) if structure.support is not None else None,
        resistance=float(structure.resistance) if structure.resistance is not None else None,
        last_swing_high=float(structure.last_swing_high) if structure.last_swing_high is not None else None,
        last_swing_low=float(structure.last_swing_low) if structure.last_swing_low is not None else None,
        structure_signal=structure_signal,
        plus_di=round(_safe(structure.plus_di), 2),
        minus_di=round(_safe(structure.minus_di), 2),
        ema_stack=_ema_stack(fe),
        mfi=round(_safe(last.get("mfi_14"), 50.0), 1),
        bb_pctb=round(_safe(last.get("bb_pctb"), 0.5), 3),
        wpr=round(_safe(last.get("wpr_14"), -50.0), 1),
        roc20=round(_safe(last.get("roc_20"), 0.0), 3),
        macd_cross=_macd_cross(fe),
        stoch_cross=_stoch_cross(fe),
        rvol=round(_rvol(fe), 3),
    )


def structure_resistance_series(fe: pd.DataFrame) -> list[float | None]:
    """Resistance candidates from the most recent swing highs."""
    zz = _zigzag(fe)
    return [p for _, p in zz if p > float(fe["close"].iloc[-1])]


def structure_support_series(fe: pd.DataFrame) -> list[float | None]:
    zz = _zigzag(fe)
    return [p for _, p in zz if p < float(fe["close"].iloc[-1])]


def _zigzag(fe: pd.DataFrame) -> list[tuple[int, float]]:
    from src.analysis.waves import zigzag

    return zigzag(fe, pct_threshold=0.5, use_atr=True)


def floats_finite(values: list[float]) -> bool:
    return all(v is not None and np.isfinite(float(v)) for v in values)
