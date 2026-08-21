from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class Trade:
    entry_time: int
    exit_time: int
    side: str
    entry: float
    exit: float
    r_multiple: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    trades: tuple[Trade, ...]
    net_r: float
    win_rate: float
    profit_factor: float
    max_drawdown_r: float


def run_fixed_bracket(
    bars: Iterable[Bar],
    side: str,
    entry: float,
    stop: float,
    target: float,
) -> BacktestResult:
    """Conservative bracket simulator.

    If stop and target are both touched by the same bar, stop wins. This
    removes optimistic same-bar ordering and avoids using future bars when
    evaluating the trade.
    """
    if side not in {"LONG", "SHORT"} or entry <= 0 or stop <= 0 or target <= 0:
        raise ValueError("invalid_backtest_inputs")
    risk = abs(entry - stop)
    if risk == 0:
        raise ValueError("zero_risk")
    trade_bars = list(bars)
    if not trade_bars:
        return BacktestResult((), 0.0, 0.0, 0.0, 0.0)
    for bar in trade_bars:
        if side == "LONG":
            stop_hit = bar.low <= stop
            target_hit = bar.high >= target
            if stop_hit:
                r = -1.0
                exit_price = stop
            elif target_hit:
                r = (target - entry) / risk
                exit_price = target
            else:
                continue
        else:
            stop_hit = bar.high >= stop
            target_hit = bar.low <= target
            if stop_hit:
                r = -1.0
                exit_price = stop
            elif target_hit:
                r = (entry - target) / risk
                exit_price = target
            else:
                continue
        trade = Trade(trade_bars[0].timestamp, bar.timestamp, side, entry, exit_price, r)
        trades = (trade,)
        wins = sum(1 for t in trades if t.r_multiple > 0)
        gross_profit = sum(t.r_multiple for t in trades if t.r_multiple > 0)
        gross_loss = -sum(t.r_multiple for t in trades if t.r_multiple < 0)
        return BacktestResult(trades, r, wins / len(trades), gross_profit / gross_loss if gross_loss else float("inf"), min(0.0, r))
    return BacktestResult((), 0.0, 0.0, 0.0, 0.0)
