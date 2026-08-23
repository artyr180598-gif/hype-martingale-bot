"""
Volatility Engine — Realized Volatility, ATR Percentiles, Squeeze Detection, and Regimes.
"""
from typing import Any

import numpy as np
import pandas as pd

from src.config.constants import VolatilityRegimeType, VolatilityTrend


class VolatilityAnalyzer:
    """
    Measures market volatility, volatility compression / expansion cycles, and assigns regimes.
    """

    @staticmethod
    def compute_volatility_metrics(df: pd.DataFrame) -> dict[str, Any]:
        """
        Extract volatility diagnostics from dataframe.
        """
        if len(df) < 30:
            return {
                "realized_vol_pct": 2.0,
                "atr_percentile": 50.0,
                "bb_width_percentile": 50.0,
                "volatility_regime": VolatilityRegimeType.NORMAL,
                "volatility_trend": VolatilityTrend.STABLE,
                "is_squeeze": False,
            }

        # 1. Realized Volatility (std of log returns)
        log_ret = np.log(df["close"] / df["close"].shift(1)).dropna()
        realized_vol = float(log_ret.rolling(window=20, min_periods=5).std().iloc[-1] * 100.0)

        # 2. ATR Percentile
        atr = df["atr_14"].dropna() if "atr_14" in df.columns else df["high"] - df["low"]
        current_atr = float(atr.iloc[-1])
        lookback_atr = atr.iloc[-min(len(atr), 100):]
        atr_percentile = float((lookback_atr < current_atr).mean() * 100.0)

        # 3. Bollinger Bandwidth Percentile
        bb_width = df["bb_width"].dropna() if "bb_width" in df.columns else pd.Series([1.0])
        current_bbw = float(bb_width.iloc[-1])
        lookback_bbw = bb_width.iloc[-min(len(bb_width), 100):]
        bbw_percentile = float((lookback_bbw < current_bbw).mean() * 100.0)

        # 4. Volatility Regime Classification
        if atr_percentile < 15.0:
            regime = VolatilityRegimeType.VERY_LOW_VOLATILITY
        elif atr_percentile < 35.0:
            regime = VolatilityRegimeType.LOW_VOLATILITY
        elif atr_percentile <= 70.0:
            regime = VolatilityRegimeType.NORMAL
        elif atr_percentile <= 90.0:
            regime = VolatilityRegimeType.HIGH_VOLATILITY
        else:
            regime = VolatilityRegimeType.EXTREME_VOLATILITY

        # 5. Volatility Trend (Expansion vs Contraction)
        atr_ema_fast = atr.ewm(span=5, adjust=False).mean().iloc[-1]
        atr_ema_slow = atr.ewm(span=20, adjust=False).mean().iloc[-1]

        if atr_ema_fast > atr_ema_slow * 1.05 and current_bbw > lookback_bbw.mean():
            vol_trend = VolatilityTrend.VOLATILITY_EXPANSION
        elif atr_ema_fast < atr_ema_slow * 0.95:
            vol_trend = VolatilityTrend.VOLATILITY_CONTRACTION
        else:
            vol_trend = VolatilityTrend.STABLE

        # Squeeze indicator: both BB Width and ATR in bottom 20th percentile
        is_squeeze = bbw_percentile < 20.0 and atr_percentile < 25.0

        return {
            "realized_vol_pct": round(realized_vol, 2),
            "atr_percentile": round(atr_percentile, 1),
            "bb_width_percentile": round(bbw_percentile, 1),
            "volatility_regime": regime,
            "volatility_trend": vol_trend,
            "is_squeeze": is_squeeze,
        }
