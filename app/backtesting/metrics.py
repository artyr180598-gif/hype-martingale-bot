from dataclasses import dataclass
from math import sqrt
from statistics import mean, pstdev


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    trades: int
    net_r: float
    win_rate: float
    profit_factor: float
    expectancy_r: float
    average_win_r: float
    average_loss_r: float
    max_drawdown_r: float
    max_winning_streak: int
    max_losing_streak: int
    sharpe_r: float
    sortino_r: float


def _streaks(values: list[float]) -> tuple[int, int]:
    best_w = best_l = cur_w = cur_l = 0
    for value in values:
        if value > 0:
            cur_w += 1
            cur_l = 0
            best_w = max(best_w, cur_w)
        elif value < 0:
            cur_l += 1
            cur_w = 0
            best_l = max(best_l, cur_l)
        else:
            cur_w = cur_l = 0
    return best_w, best_l


def calculate_metrics(r_multiples: list[float]) -> PerformanceMetrics:
    if not r_multiples:
        return PerformanceMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0)
    wins = [r for r in r_multiples if r > 0]
    losses = [r for r in r_multiples if r < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    equity = peak = 0.0
    max_dd = 0.0
    for r in r_multiples:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    avg = mean(r_multiples)
    std = pstdev(r_multiples) if len(r_multiples) > 1 else 0.0
    downside = [min(0.0, r) for r in r_multiples]
    downside_std = pstdev(downside) if len(downside) > 1 else 0.0
    sharpe = avg / std * sqrt(len(r_multiples)) if std else 0.0
    sortino = avg / downside_std * sqrt(len(r_multiples)) if downside_std else 0.0
    best_w, best_l = _streaks(r_multiples)
    return PerformanceMetrics(
        trades=len(r_multiples),
        net_r=sum(r_multiples),
        win_rate=len(wins) / len(r_multiples),
        profit_factor=gross_profit / gross_loss if gross_loss else float("inf"),
        expectancy_r=avg,
        average_win_r=mean(wins) if wins else 0.0,
        average_loss_r=mean(losses) if losses else 0.0,
        max_drawdown_r=-max_dd,
        max_winning_streak=best_w,
        max_losing_streak=best_l,
        sharpe_r=sharpe,
        sortino_r=sortino,
    )
