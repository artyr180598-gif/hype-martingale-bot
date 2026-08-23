"""
Strategy Benchmarks and Baseline Models (Buy & Hold, Simple Trend, Random Entry).
"""
from typing import Any

from src.data.models import CandleData


class BenchmarkEvaluator:
    """
    Evaluates baseline strategies to establish true alpha value.
    """

    @staticmethod
    def buy_and_hold_return(candles: list[CandleData], initial_balance: float = 10000.0) -> dict[str, Any]:
        if not candles:
            return {"return_pct": 0.0, "final_equity": initial_balance}

        start_p = candles[0].close
        end_p = candles[-1].close
        ret_pct = ((end_p - start_p) / start_p) * 100.0
        final_equity = initial_balance * (1.0 + ret_pct / 100.0)

        # Max drawdown of buy and hold
        closes = [c.close for c in candles]
        peak = closes[0]
        max_dd = 0.0
        for p in closes:
            peak = max(peak, p)
            dd = (peak - p) / peak * 100.0
            max_dd = max(max_dd, dd)

        return {
            "name": "Buy & Hold Benchmark",
            "return_pct": round(ret_pct, 2),
            "final_equity": round(final_equity, 2),
            "max_drawdown_pct": round(max_dd, 2),
        }
