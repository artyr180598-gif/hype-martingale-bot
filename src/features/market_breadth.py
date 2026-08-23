"""
Market Breadth Engine — Cross-Sectional Market Health and Participation.
"""
from typing import Any

import pandas as pd


class MarketBreadthEngine:
    """
    Measures market-wide breadth metrics across tracked cryptocurrency futures.
    """

    @staticmethod
    def calculate_breadth(symbol_dfs: dict[str, pd.DataFrame]) -> dict[str, Any]:
        """
        Aggregate breadth metrics across multiple asset dataframes.
        """
        if not symbol_dfs:
            return {
                "pct_above_ema50": 50.0,
                "pct_above_ema200": 50.0,
                "advance_decline_ratio": 1.0,
                "breadth_state": "NEUTRAL",
                "advancers_count": 0,
                "decliners_count": 0,
            }

        above_50 = 0
        above_200 = 0
        advancers = 0
        decliners = 0
        total = len(symbol_dfs)

        for sym, df in symbol_dfs.items():
            if len(df) < 50:
                continue
            cur_close = float(df["close"].iloc[-1])
            prev_close = float(df["close"].iloc[-2]) if len(df) > 1 else cur_close
            ema50 = float(df["ema_50"].iloc[-1]) if "ema_50" in df.columns else cur_close
            ema200 = float(df["ema_200"].iloc[-1]) if "ema_200" in df.columns else cur_close

            if cur_close > ema50:
                above_50 += 1
            if cur_close > ema200:
                above_200 += 1

            if cur_close > prev_close:
                advancers += 1
            elif cur_close < prev_close:
                decliners += 1

        pct_50 = (above_50 / max(1, total)) * 100.0
        pct_200 = (above_200 / max(1, total)) * 100.0
        ad_ratio = advancers / max(1, decliners)

        if pct_50 >= 70.0 and ad_ratio >= 1.5:
            state = "BULLISH_EXPANSION"
        elif pct_50 <= 30.0 and ad_ratio <= 0.67:
            state = "BEARISH_CONTRACTION"
        else:
            state = "MIXED_NEUTRAL"

        return {
            "pct_above_ema50": round(pct_50, 1),
            "pct_above_ema200": round(pct_200, 1),
            "advance_decline_ratio": round(ad_ratio, 2),
            "breadth_state": state,
            "advancers_count": advancers,
            "decliners_count": decliners,
            "total_tracked": total,
        }
