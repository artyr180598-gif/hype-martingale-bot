"""
Historical Analog Engine — K-Nearest Historical Regime & Pattern Matching.
"""
from typing import Any

import numpy as np
import pandas as pd


class HistoricalAnalogEngine:
    """
    Finds historical analog bars matching the current multidimensional feature state
    and calculates forward statistical expectancy and win rate.
    """

    FEATURE_KEYS = ["rsi_14", "adx_14", "atr_pct", "vwap_dist_pct", "bb_percent_b"]

    @classmethod
    def extract_feature_vector(cls, features: dict[str, Any]) -> np.ndarray:
        vec = []
        for k in cls.FEATURE_KEYS:
            val = float(features.get(k, 0.0))
            vec.append(val)
        return np.array(vec, dtype=float)

    @classmethod
    def evaluate_historical_analogs(
        cls,
        current_features: dict[str, Any],
        historical_df: pd.DataFrame | None = None,
        top_k: int = 50,
        forward_bars: int = 15,
    ) -> dict[str, Any]:
        """
        Match current state against historical candles to compute empirical edge.
        """
        if historical_df is None or len(historical_df) < 150:
            return {
                "analog_count": 0,
                "win_rate_pct": 55.0,
                "expectancy_r": 0.35,
                "positive_outcomes": 0,
                "negative_outcomes": 0,
            }

        df = historical_df.copy()
        current_vec = cls.extract_feature_vector(current_features)

        # Check if feature columns exist in historical df
        missing = [k for k in cls.FEATURE_KEYS if k not in df.columns]
        if missing:
            return {
                "analog_count": 0,
                "win_rate_pct": 55.0,
                "expectancy_r": 0.35,
                "positive_outcomes": 0,
                "negative_outcomes": 0,
            }

        # Build feature matrix for historical bars (excluding last 20 to avoid test leakage)
        valid_len = len(df) - forward_bars - 1
        if valid_len < 50:
            return {
                "analog_count": 0,
                "win_rate_pct": 55.0,
                "expectancy_r": 0.35,
                "positive_outcomes": 0,
                "negative_outcomes": 0,
            }

        hist_matrix = df[cls.FEATURE_KEYS].iloc[:valid_len].values

        # Normalize features with std
        std = np.std(hist_matrix, axis=0) + 1e-6
        norm_hist = hist_matrix / std
        norm_curr = current_vec / std

        # Compute Euclidean distances
        distances = np.linalg.norm(norm_hist - norm_curr, axis=1)
        nearest_indices = np.argsort(distances)[:top_k]

        # Evaluate forward performance of the matched analogs
        positive_count = 0
        negative_count = 0
        r_multiples = []

        is_long = current_features.get("direction", "LONG") == "LONG"

        for idx in nearest_indices:
            entry_p = float(df["close"].iloc[idx])
            future_high = float(df["high"].iloc[idx + 1 : idx + 1 + forward_bars].max())
            future_low = float(df["low"].iloc[idx + 1 : idx + 1 + forward_bars].min())
            future_close = float(df["close"].iloc[idx + forward_bars])

            risk = entry_p * 0.015  # 1.5% assumed risk unit

            if is_long:
                max_gain = future_high - entry_p
                max_loss = entry_p - future_low
                net_gain = future_close - entry_p
            else:
                max_gain = entry_p - future_low
                max_loss = future_high - entry_p
                net_gain = entry_p - future_close

            r = net_gain / risk
            r_multiples.append(r)

            if max_gain > max_loss and net_gain > 0:
                positive_count += 1
            else:
                negative_count += 1

        total_matches = len(r_multiples)
        win_rate = (positive_count / max(1, total_matches)) * 100.0
        exp_r = float(np.mean(r_multiples)) if r_multiples else 0.0

        return {
            "analog_count": total_matches,
            "win_rate_pct": round(win_rate, 1),
            "expectancy_r": round(exp_r, 2),
            "positive_outcomes": positive_count,
            "negative_outcomes": negative_count,
        }
