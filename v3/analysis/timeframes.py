"""Multi-timeframe analysis.

Builds a ``TimeframeView`` for every configured timeframe using the same
indicator library as the live v1 engine.  Price/indicator values at bar *i*
depend only on bars ``<= i`` so this module is safe to call from the backtester.
"""

from __future__ import annotations

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

    # BOS / CHoCH heuristic using recent swing structure
    structure_signal = "none"
    swings_high = [p for p in structure_resistance_series(fe) if p is not None]
    swings_low = [p for p in structure_support_series(fe) if p is not None]
    if len(swings_high) >= 2 and len(swings_low) >= 2:
        if floats_finite(swings_high[-2:]) and swings_high[-1] > swings_high[-2]:
            structure_signal = "BOS_UP"
        elif len(swings_low) >= 2 and swings_low[-1] > swings_low[-2]:
            structure_signal = "CHoCH_UP"
        elif len(swings_high) >= 2 and swings_high[-1] < swings_high[-2]:
            structure_signal = "BOS_DOWN"
        elif len(swings_low) >= 2 and swings_low[-1] < swings_low[-2]:
            structure_signal = "CHoCH_DOWN"

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
