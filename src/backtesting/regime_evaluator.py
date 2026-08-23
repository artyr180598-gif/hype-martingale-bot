"""
Regime-Based Strategy Performance Evaluator.
"""
from typing import Any

import numpy as np


class RegimePerformanceEvaluator:
    """
    Analyzes strategy win rate and profit factor across bull, bear, range, and high-volatility regimes.
    """

    @staticmethod
    def evaluate_by_regime(trades: list[dict[str, Any]]) -> dict[str, Any]:
        if not trades:
            return {}

        regimes: dict[str, dict[str, Any]] = {}
        for t in trades:
            r = str(t.get("market_regime", "RANGE"))
            if r not in regimes:
                regimes[r] = {"trades": 0, "wins": 0, "pnl": 0.0, "realized_rs": []}
            regimes[r]["trades"] = int(regimes[r]["trades"]) + 1
            net_pnl = float(t.get("net_pnl", 0.0))
            if net_pnl > 0:
                regimes[r]["wins"] = int(regimes[r]["wins"]) + 1
            regimes[r]["pnl"] = float(regimes[r]["pnl"]) + net_pnl
            realized_rs_list: list[float] = regimes[r]["realized_rs"]
            realized_rs_list.append(float(t.get("realized_r", 0.0)))

        report: dict[str, Any] = {}
        for reg, data in regimes.items():
            cnt = int(data["trades"])
            wins = int(data["wins"])
            pnl = float(data["pnl"])
            realized_rs: list[float] = data["realized_rs"]
            wr = (wins / max(1, cnt)) * 100.0
            avg_r = float(np.mean(realized_rs)) if realized_rs else 0.0
            report[reg] = {
                "trades_count": cnt,
                "win_rate_pct": round(wr, 1),
                "total_pnl_usd": round(pnl, 2),
                "avg_expectancy_r": round(avg_r, 2),
            }

        return report
