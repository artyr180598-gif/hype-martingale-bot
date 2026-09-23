"""Trend indicators — EMA, SMA, SuperTrend, ADX, PSAR, Ichimoku (ported from freqtrade/jesse + CryptoScanBot SBM)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def compute_trend_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute trend indicators for a OHLCV dataframe.
    Adds columns: ema9, ema20, ema50, ema200, sma20, sma50, sma200, adx, plus_di, minus_di,
    supertrend, supertrend_dir, psar, ichimoku_*
    """
    if df.empty or len(df) < 50:
        return df

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # EMA / SMA (SBM uses 20/50/200 alignment)
    df["ema9"] = ema(close, 9)
    df["ema20"] = ema(close, 20)
    df["ema21"] = ema(close, 21)
    df["ema50"] = ema(close, 50)
    df["ema200"] = ema(close, 200)
    df["sma20"] = sma(close, 20)
    df["sma50"] = sma(close, 50)
    df["sma200"] = sma(close, 200)

    # ADX (trend strength)
    try:
        # Manual ADX calculation (Wilder)
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        up_move = high - high.shift()
        down_move = low.shift() - low

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_dm = pd.Series(plus_dm, index=df.index).rolling(14).mean()
        minus_dm = pd.Series(minus_dm, index=df.index).rolling(14).mean()

        plus_di = 100 * (plus_dm / atr)
        minus_di = 100 * (minus_dm / atr)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.rolling(14).mean()

        df["adx"] = adx
        df["plus_di"] = plus_di
        df["minus_di"] = minus_di
    except Exception:
        df["adx"] = 0
        df["plus_di"] = 0
        df["minus_di"] = 0

    # SuperTrend (2.76 factor from best performing combo)
    try:
        period = 10
        multiplier = 3.0
        hl2 = (high + low) / 2
        atr = (high - low).rolling(period).mean()
        # Use Wilder ATR approx
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean()

        upper_band = hl2 + multiplier * atr
        lower_band = hl2 - multiplier * atr

        direction = pd.Series(1, index=df.index)
        supertrend = pd.Series(0.0, index=df.index)

        for i in range(1, len(df)):
            # Simplified SuperTrend logic
            if close.iloc[i] <= lower_band.iloc[i - 1]:
                direction.iloc[i] = -1
            elif close.iloc[i] >= upper_band.iloc[i - 1]:
                direction.iloc[i] = 1
            else:
                direction.iloc[i] = direction.iloc[i - 1]

            if direction.iloc[i] == 1:
                supertrend.iloc[i] = lower_band.iloc[i]
            else:
                supertrend.iloc[i] = upper_band.iloc[i]

        df["supertrend"] = supertrend
        df["supertrend_dir"] = direction  # 1 = bull, -1 = bear
    except Exception:
        df["supertrend"] = close
        df["supertrend_dir"] = 1

    # PSAR (Parabolic SAR) — simplified
    try:
        psar = pd.Series(close.values, index=df.index)
        # Very simplified PSAR: use EMA20 as proxy for direction, then trail
        # Real PSAR is complex; for SBM we need PSAR position relative to price
        # We'll compute incremental SAR
        af = 0.02
        max_af = 0.2
        # Placeholder: PSAR below price = uptrend, above = downtrend
        # Use SMA20 vs close to infer
        df["psar"] = df["sma20"] * 0.99  # approximation, will be refined in structure
        df["psar_dir"] = np.where(close > df["psar"], 1, -1)
    except Exception:
        df["psar"] = close
        df["psar_dir"] = 1

    # Ichimoku (simplified)
    try:
        tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
        kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
        senkou_a = ((tenkan + kijun) / 2).shift(26)
        senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
        df["ichimoku_tenkan"] = tenkan
        df["ichimoku_kijun"] = kijun
        df["ichimoku_senkou_a"] = senkou_a
        df["ichimoku_senkou_b"] = senkou_b
        df["ichimoku_cloud_green"] = (senkou_a > senkou_b).astype(int)
    except Exception:
        df["ichimoku_tenkan"] = close
        df["ichimoku_kijun"] = close

    return df


def is_uptrend(df: pd.DataFrame, lookback: int = 3) -> bool:
    if len(df) < lookback:
        return False
    try:
        # EMA alignment 20 > 50 > 200 and price above
        last = df.iloc[-1]
        return (
            last["ema20"] > last["ema50"] > last["ema200"]
            and last["close"] > last["ema20"]
            and last["adx"] > 20
        )
    except Exception:
        return False


def is_downtrend(df: pd.DataFrame) -> bool:
    if len(df) < 3:
        return False
    try:
        last = df.iloc[-1]
        return (
            last["ema20"] < last["ema50"] < last["ema200"]
            and last["close"] < last["ema20"]
            and last["adx"] > 20
        )
    except Exception:
        return False
