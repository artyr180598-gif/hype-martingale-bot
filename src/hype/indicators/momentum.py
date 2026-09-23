"""Momentum indicators — RSI, Stochastic, StochRSI, MFI, Williams %R, CCI, MACD, AO."""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series.fillna(50)


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3) -> tuple[pd.Series, pd.Series]:
    lowest_low = low.rolling(k).min()
    highest_high = high.rolling(k).max()
    k_percent = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    d_percent = k_percent.rolling(d).mean()
    return k_percent.fillna(50), d_percent.fillna(50)


def compute_momentum_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 30:
        return df

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # RSI
    df["rsi"] = rsi(close, 14)
    df["rsi_7"] = rsi(close, 7)
    df["rsi_21"] = rsi(close, 21)

    # Stochastic
    stoch_k, stoch_d = stochastic(high, low, close, 14, 3)
    df["stoch_k"] = stoch_k
    df["stoch_d"] = stoch_d
    # Fast
    stoch_k_fast, stoch_d_fast = stochastic(high, low, close, 5, 3)
    df["stoch_k_fast"] = stoch_k_fast

    # StochRSI
    try:
        rsi_series = df["rsi"]
        stochrsi_k = (rsi_series - rsi_series.rolling(14).min()) / (rsi_series.rolling(14).max() - rsi_series.rolling(14).min()).replace(0, np.nan) * 100
        df["stochrsi_k"] = stochrsi_k.fillna(50)
        df["stochrsi_d"] = df["stochrsi_k"].rolling(3).mean().fillna(50)
    except Exception:
        df["stochrsi_k"] = 50
        df["stochrsi_d"] = 50

    # MFI (Money Flow Index) — volume weighted RSI
    try:
        typical_price = (high + low + close) / 3
        raw_money_flow = typical_price * volume
        pos_flow = raw_money_flow.where(typical_price > typical_price.shift(), 0).rolling(14).sum()
        neg_flow = raw_money_flow.where(typical_price < typical_price.shift(), 0).rolling(14).sum()
        mfi_ratio = pos_flow / neg_flow.replace(0, np.nan)
        mfi = 100 - (100 / (1 + mfi_ratio))
        df["mfi"] = mfi.fillna(50)
    except Exception:
        df["mfi"] = 50

    # Williams %R
    try:
        highest_high = high.rolling(14).max()
        lowest_low = low.rolling(14).min()
        wr = -100 * (highest_high - close) / (highest_high - lowest_low).replace(0, np.nan)
        df["willr"] = wr.fillna(-50)
    except Exception:
        df["willr"] = -50

    # CCI
    try:
        tp = (high + low + close) / 3
        sma_tp = tp.rolling(20).mean()
        mad = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=False)
        cci = (tp - sma_tp) / (0.015 * mad).replace(0, np.nan)
        df["cci"] = cci.fillna(0)
    except Exception:
        df["cci"] = 0

    # MACD
    try:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal
        df["macd"] = macd_line
        df["macd_signal"] = signal
        df["macd_hist"] = hist
        df["macd_hist_trend"] = hist.diff()
    except Exception:
        df["macd"] = 0
        df["macd_signal"] = 0
        df["macd_hist"] = 0

    # Awesome Oscillator
    try:
        mid = (high + low) / 2
        ao = mid.rolling(5).mean() - mid.rolling(34).mean()
        df["ao"] = ao.fillna(0)
    except Exception:
        df["ao"] = 0

    # RSI divergence helper (price vs RSI)
    try:
        df["rsi_div_bull"] = (close.diff(5) < 0) & (df["rsi"].diff(5) > 0)
        df["rsi_div_bear"] = (close.diff(5) > 0) & (df["rsi"].diff(5) < 0)
    except Exception:
        df["rsi_div_bull"] = False
        df["rsi_div_bear"] = False

    return df


def is_oversold_momentum(df: pd.DataFrame) -> bool:
    """STOBB-like oversold check"""
    if df.empty:
        return False
    last = df.iloc[-1]
    try:
        return (
            last["rsi"] < 35
            and last["stoch_k"] < 25
            and last["stoch_d"] < 25
            and last["mfi"] < 35
        )
    except Exception:
        return False


def is_overbought_momentum(df: pd.DataFrame) -> bool:
    if df.empty:
        return False
    last = df.iloc[-1]
    try:
        return (
            last["rsi"] > 65
            and last["stoch_k"] > 75
            and last["stoch_d"] > 75
            and last["mfi"] > 65
        )
    except Exception:
        return False
