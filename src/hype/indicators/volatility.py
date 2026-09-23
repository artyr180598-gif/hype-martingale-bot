"""Volatility indicators — ATR, Bollinger, Keltner, Donchian, Squeeze."""

from __future__ import annotations

import numpy as np
import pandas as pd


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_volatility_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 20:
        return df

    high = df["high"]
    low = df["low"]
    close = df["close"]

    # ATR
    df["atr"] = atr(high, low, close, 14)
    df["atr_pct"] = df["atr"] / close * 100
    df["atr_50"] = atr(high, low, close, 50)

    # Bollinger Bands (20,2) — core for STOBB
    try:
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        df["bb_middle"] = sma20
        df["bb_upper"] = sma20 + 2 * std20
        df["bb_lower"] = sma20 - 2 * std20
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / sma20 * 100
        df["bb_pct"] = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)  # %B
        df["bb_pct"] = df["bb_pct"].fillna(0.5)
        # % distance from bands
        df["bb_pos"] = df["bb_pct"]  # 0 = lower, 1 = upper
    except Exception:
        df["bb_middle"] = close
        df["bb_upper"] = close * 1.02
        df["bb_lower"] = close * 0.98
        df["bb_width"] = 2.0
        df["bb_pct"] = 0.5

    # Keltner Channel
    try:
        ema20 = close.ewm(span=20, adjust=False).mean()
        atr20 = atr(high, low, close, 20)
        df["kc_middle"] = ema20
        df["kc_upper"] = ema20 + 1.5 * atr20
        df["kc_lower"] = ema20 - 1.5 * atr20
    except Exception:
        df["kc_middle"] = close
        df["kc_upper"] = close * 1.015
        df["kc_lower"] = close * 0.985

    # Donchian
    try:
        df["donchian_upper_20"] = high.rolling(20).max()
        df["donchian_lower_20"] = low.rolling(20).min()
        df["donchian_mid_20"] = (df["donchian_upper_20"] + df["donchian_lower_20"]) / 2
    except Exception:
        df["donchian_upper_20"] = close
        df["donchian_lower_20"] = close

    # Squeeze (BB inside KC = squeeze, outside = release) — TTM Squeeze
    try:
        df["squeeze_on"] = (df["bb_lower"] > df["kc_lower"]) & (df["bb_upper"] < df["kc_upper"])
        df["squeeze_off"] = ~df["squeeze_on"]
        # Count squeeze duration
        df["squeeze_count"] = df["squeeze_on"].astype(int).groupby((~df["squeeze_on"]).cumsum()).cumsum()
        # Squeeze release momentum
        df["squeeze_release"] = df["squeeze_on"].shift(1) & df["squeeze_off"]
    except Exception:
        df["squeeze_on"] = False
        df["squeeze_off"] = True
        df["squeeze_release"] = False

    # Volatility compression ratio
    try:
        df["volatility_compression"] = df["atr"] / df["atr_50"]
    except Exception:
        df["volatility_compression"] = 1.0

    return df


def is_squeeze_release(df: pd.DataFrame) -> bool:
    if df.empty or len(df) < 2:
        return False
    try:
        return bool(df.iloc[-1]["squeeze_release"])
    except Exception:
        return False


def bb_position(close: float, lower: float, upper: float) -> float:
    """0 = at lower, 1 = at upper"""
    if upper == lower:
        return 0.5
    return (close - lower) / (upper - lower)
