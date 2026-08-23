"""
Correlation and Cross-Asset Relative Performance Engine.
"""
from typing import Any

import numpy as np
import pandas as pd


class CorrelationAnalyzer:
    """
    Computes asset correlation and beta against BTC benchmark.
    """

    @staticmethod
    def compute_btc_correlation(
        asset_closes: pd.Series,
        btc_closes: pd.Series,
        window: int = 30,
    ) -> dict[str, Any]:
        """
        Compute rolling Pearson correlation and Beta relative to BTC.
        """
        min_len = min(len(asset_closes), len(btc_closes))
        if min_len < 10:
            return {"correlation": 0.80, "beta": 1.0, "relative_strength": "NEUTRAL"}

        a_ret = asset_closes.iloc[-min_len:].pct_change().dropna()
        b_ret = btc_closes.iloc[-min_len:].pct_change().dropna()

        # Align lengths
        common_len = min(len(a_ret), len(b_ret))
        a_ret = a_ret.iloc[-common_len:]
        b_ret = b_ret.iloc[-common_len:]

        # Correlation
        corr = float(a_ret.corr(b_ret))
        if np.isnan(corr):
            corr = 0.80

        # Beta = Cov(A, BTC) / Var(BTC)
        var_b = float(b_ret.var())
        cov_ab = float(a_ret.cov(b_ret))
        beta = float(cov_ab / var_b) if var_b > 1e-8 else 1.0
        if np.isnan(beta):
            beta = 1.0

        # Relative strength over last 20 bars
        a_perf = float((asset_closes.iloc[-1] / asset_closes.iloc[-min(20, len(asset_closes))] - 1.0) * 100.0)
        b_perf = float((btc_closes.iloc[-1] / btc_closes.iloc[-min(20, len(btc_closes))] - 1.0) * 100.0)
        diff = a_perf - b_perf

        if diff > 3.0:
            rel_strength = "OUTPERFORMING_BTC"
        elif diff < -3.0:
            rel_strength = "UNDERPERFORMING_BTC"
        else:
            rel_strength = "IN_LINE_WITH_BTC"

        return {
            "correlation": round(corr, 3),
            "beta": round(beta, 2),
            "relative_strength": rel_strength,
            "relative_perf_pct": round(diff, 2),
        }
