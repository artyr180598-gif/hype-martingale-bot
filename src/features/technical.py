"""
Technical Analysis Features and Vectorized Quantitative Indicators.
"""

import numpy as np
import pandas as pd


class TechnicalIndicators:
    """
    High-performance vectorized technical indicator calculations.
    """

    @staticmethod
    def compute_all(df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute full suite of technical indicators on an OHLCV dataframe.
        Expects columns: open, high, low, close, volume.
        """
        if len(df) < 20:
            return df

        df = df.copy()

        # 1. Moving Averages
        df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
        df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()
        df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
        df["sma_50"] = df["close"].rolling(window=50, min_periods=10).mean()
        df["sma_200"] = df["close"].rolling(window=200, min_periods=20).mean()

        # EMA Trend Alignments
        df["ema_9_gt_21"] = (df["ema_9"] > df["ema_21"]).astype(int)
        df["ema_21_gt_50"] = (df["ema_21"] > df["ema_50"]).astype(int)
        df["price_gt_ema_200"] = (df["close"] > df["ema_200"]).astype(int)

        # 2. VWAP (Volume Weighted Average Price)
        typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
        cum_vol = df["volume"].cumsum()
        cum_vp = (typical_price * df["volume"]).cumsum()
        df["vwap"] = np.where(cum_vol > 0, cum_vp / cum_vol, df["close"])
        df["vwap_dist_pct"] = ((df["close"] - df["vwap"]) / df["vwap"]) * 100.0

        # 3. RSI (14)
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = np.where(loss == 0, 100.0, gain / loss)
        df["rsi_14"] = np.where(loss == 0, 100.0, 100 - (100 / (1 + rs)))

        # 4. MACD (12, 26, 9)
        ema_12 = df["close"].ewm(span=12, adjust=False).mean()
        ema_26 = df["close"].ewm(span=26, adjust=False).mean()
        df["macd_line"] = ema_12 - ema_26
        df["macd_signal"] = df["macd_line"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = df["macd_line"] - df["macd_signal"]

        # 5. ATR (14) & True Range
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["true_range"] = tr
        df["atr_14"] = tr.rolling(window=14, min_periods=5).mean()
        df["atr_pct"] = (df["atr_14"] / df["close"]) * 100.0

        # 6. Bollinger Bands (20, 2)
        bb_middle = df["close"].rolling(window=20, min_periods=5).mean()
        bb_std = df["close"].rolling(window=20, min_periods=5).std()
        df["bb_middle"] = bb_middle
        df["bb_upper"] = bb_middle + (bb_std * 2.0)
        df["bb_lower"] = bb_middle - (bb_std * 2.0)
        df["bb_width"] = np.where(bb_middle > 0, (df["bb_upper"] - df["bb_lower"]) / bb_middle * 100.0, 0.0)
        df["bb_percent_b"] = np.where(
            (df["bb_upper"] - df["bb_lower"]) > 0,
            (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]),
            0.5,
        )

        # 7. ADX (14) & Directional Movement
        high_diff = df["high"].diff()
        low_diff = -df["low"].diff()
        pos_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
        neg_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)

        tr_smooth = tr.ewm(alpha=1/14, adjust=False).mean()
        pos_di = (pd.Series(pos_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / tr_smooth) * 100.0
        neg_di = (pd.Series(neg_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / tr_smooth) * 100.0

        dx_denom = (pos_di + neg_di).replace(0, 1.0)
        dx = ((pos_di - neg_di).abs() / dx_denom) * 100.0
        df["plus_di"] = pos_di
        df["minus_di"] = neg_di
        df["adx_14"] = dx.rolling(window=14, min_periods=5).mean()

        # 8. Stochastic (14, 3)
        low_14 = df["low"].rolling(window=14, min_periods=5).min()
        high_14 = df["high"].rolling(window=14, min_periods=5).max()
        stoch_denom = (high_14 - low_14).replace(0, 1.0)
        df["stoch_k"] = ((df["close"] - low_14) / stoch_denom) * 100.0
        df["stoch_d"] = df["stoch_k"].rolling(window=3, min_periods=1).mean()

        return df


def calculate_fibonacci_levels(high: float, low: float) -> dict[str, float]:
    """Calculate Fibonacci retracement and extension levels between a swing high and low."""
    diff = high - low
    return {
        "fib_0": low,
        "fib_0.236": low + 0.236 * diff,
        "fib_0.382": low + 0.382 * diff,
        "fib_0.500": low + 0.500 * diff,
        "fib_0.618": low + 0.618 * diff,
        "fib_0.786": low + 0.786 * diff,
        "fib_1.0": high,
        "fib_1.272": high + 0.272 * diff,
        "fib_1.618": high + 0.618 * diff,
    }
