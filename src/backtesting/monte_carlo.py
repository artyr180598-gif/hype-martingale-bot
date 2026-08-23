"""
Monte Carlo Simulation Engine — Trade Order Permutation, Drawdown Distributions, and Risk of Ruin.
"""
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class MonteCarloReport:
    total_simulations: int
    confidence_level_pct: float
    simulated_median_return_pct: float
    max_drawdown_50th_percentile: float
    max_drawdown_95th_percentile: float
    max_drawdown_99th_percentile: float
    risk_of_ruin_probability_pct: float
    median_ending_equity: float
    worst_case_ending_equity: float


class MonteCarloSimulator:
    """
    Simulates thousands of resampled trade permutations to assess tail risk and ruin probability.
    """

    @classmethod
    def run_simulation(
        cls,
        trades: list[dict[str, Any]],
        initial_balance: float = 10000.0,
        num_simulations: int = 1000,
        ruin_threshold_pct: float = 50.0,
    ) -> MonteCarloReport:
        if not trades or len(trades) < 5:
            return MonteCarloReport(
                total_simulations=num_simulations,
                confidence_level_pct=95.0,
                simulated_median_return_pct=0.0,
                max_drawdown_50th_percentile=0.0,
                max_drawdown_95th_percentile=0.0,
                max_drawdown_99th_percentile=0.0,
                risk_of_ruin_probability_pct=0.0,
                median_ending_equity=initial_balance,
                worst_case_ending_equity=initial_balance,
            )

        pnls = [t.get("net_pnl", 0.0) for t in trades]
        ruin_balance = initial_balance * (1.0 - ruin_threshold_pct / 100.0)

        ending_equities = []
        max_drawdowns = []
        ruin_count = 0

        for _ in range(num_simulations):
            # Resample trades with replacement (bootstrap)
            resampled = np.random.choice(pnls, size=len(pnls), replace=True)
            equity_curve = [initial_balance]
            cur_eq = initial_balance
            hit_ruin = False

            peak = initial_balance
            sim_max_dd = 0.0

            for pnl in resampled:
                cur_eq += pnl
                if cur_eq <= ruin_balance:
                    hit_ruin = True
                peak = max(peak, cur_eq)
                dd = (peak - cur_eq) / peak * 100.0 if peak > 0 else 0.0
                sim_max_dd = max(sim_max_dd, dd)
                equity_curve.append(cur_eq)

            if hit_ruin:
                ruin_count += 1

            ending_equities.append(cur_eq)
            max_drawdowns.append(sim_max_dd)

        ending_equities.sort()
        max_drawdowns.sort()

        median_eq = float(np.median(ending_equities))
        worst_eq = float(ending_equities[0])
        median_ret = ((median_eq - initial_balance) / initial_balance) * 100.0

        dd_50 = float(np.percentile(max_drawdowns, 50))
        dd_95 = float(np.percentile(max_drawdowns, 95))
        dd_99 = float(np.percentile(max_drawdowns, 99))
        ruin_prob = (ruin_count / num_simulations) * 100.0

        return MonteCarloReport(
            total_simulations=num_simulations,
            confidence_level_pct=95.0,
            simulated_median_return_pct=round(median_ret, 2),
            max_drawdown_50th_percentile=round(dd_50, 2),
            max_drawdown_95th_percentile=round(dd_95, 2),
            max_drawdown_99th_percentile=round(dd_99, 2),
            risk_of_ruin_probability_pct=round(ruin_prob, 2),
            median_ending_equity=round(median_eq, 2),
            worst_case_ending_equity=round(worst_eq, 2),
        )
