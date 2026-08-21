from dataclasses import dataclass
import random


@dataclass(frozen=True, slots=True)
class MonteCarloSummary:
    simulations: int
    median_final_r: float
    p05_final_r: float
    p95_final_r: float
    median_max_drawdown_r: float
    p95_max_drawdown_r: float


def simulate(r_multiples: list[float], simulations: int = 5000, seed: int = 42) -> MonteCarloSummary:
    if simulations <= 0:
        raise ValueError("simulations_must_be_positive")
    if not r_multiples:
        return MonteCarloSummary(simulations, 0.0, 0.0, 0.0, 0.0, 0.0)
    rng = random.Random(seed)
    finals: list[float] = []
    drawdowns: list[float] = []
    for _ in range(simulations):
        sample = list(r_multiples)
        rng.shuffle(sample)
        equity = peak = max_dd = 0.0
        for value in sample:
            equity += value
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        finals.append(equity)
        drawdowns.append(-max_dd)
    finals.sort()
    drawdowns.sort()
    q = lambda values, p: values[min(len(values) - 1, int((len(values) - 1) * p))]
    return MonteCarloSummary(
        simulations=simulations,
        median_final_r=q(finals, 0.50),
        p05_final_r=q(finals, 0.05),
        p95_final_r=q(finals, 0.95),
        median_max_drawdown_r=q(drawdowns, 0.50),
        p95_max_drawdown_r=q(drawdowns, 0.05),
    )
