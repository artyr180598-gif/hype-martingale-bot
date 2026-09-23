"""Volume indicators — OBV, CVD, VWAP, VWMA, Volume Z, RVOL."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_volume_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 20:
        return df

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # OBV
    try:
        obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
        df["obv"] = obv
        df["obv_ema20"] = obv.ewm(span=20, adjust=False).mean()
    except Exception:
        df["obv"] = 0
        df["obv_ema20"] = 0

    # VWAP (session VWAP approximated as cumulative)
    try:
        tp = (high + low + close) / 3
        df["vwap"] = (tp * volume).cumsum() / volume.cumsum().replace(0, np.nan)
        df["vwap"] = df["vwap"].fillna(close)
        df["price_vs_vwap"] = (close - df["vwap"]) / df["vwap"] * 100
        df["price_vs_vwap_atr"] = (close - df["vwap"]) / df.get("atr", close * 0.01)
    except Exception:
        df["vwap"] = close
        df["price_vs_vwap"] = 0

    # VWMA
    try:
        df["vwma20"] = (close * volume).rolling(20).sum() / volume.rolling(20).sum().replace(0, np.nan)
        df["vwma20"] = df["vwma20"].fillna(close)
    except Exception:
        df["vwma20"] = close

    # Volume SMA and Z-score
    try:
        vol_sma20 = volume.rolling(20).mean()
        vol_std20 = volume.rolling(20).std()
        df["volume_sma20"] = vol_sma20
        df["volume_z"] = (volume - vol_sma20) / vol_std20.replace(0, np.nan)
        df["volume_z"] = df["volume_z"].fillna(0)
        df["volume_ratio"] = volume / vol_sma20.replace(0, np.nan)
        df["volume_ratio"] = df["volume_ratio"].fillna(1.0)
    except Exception:
        df["volume_sma20"] = volume
        df["volume_z"] = 0
        df["volume_ratio"] = 1.0

    # RVOL (Relative Volume) — compare to same period average
    try:
        df["rvol"] = volume / volume.rolling(20).mean().replace(0, np.nan)
        df["rvol"] = df["rvol"].fillna(1.0)
    except Exception:
        df["rvol"] = 1.0

    # Volume spike
    df["volume_spike"] = df["volume_ratio"] > 1.5

    # Money flow (close location value)
    try:
        clv = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
        clv = clv.fillna(0)
        mf = clv * volume
        df["cmf_20"] = mf.rolling(20).sum() / volume.rolling(20).sum().replace(0, np.nan)
        df["cmf_20"] = df["cmf_20"].fillna(0)
    except Exception:
        df["cmf_20"] = 0

    return df


def is_volume_confirmed(df: pd.DataFrame, direction: str = "long") -> bool:
    if df.empty:
        return False
    try:
        last = df.iloc[-1]
        if direction == "long":
            return last["volume_ratio"] > 1.0 and last["cmf_20"] > -0.1
        else:
            return last["volume_ratio"] > 1.0 and last["cmf_20"] < 0.1
    except Exception:
        return False
