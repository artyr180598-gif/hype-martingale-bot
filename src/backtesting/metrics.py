"""
Comprehensive Quantitative Backtest Performance Metrics.
"""
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class BacktestMetrics:
    initial_balance: float
    final_equity: float
    net_profit_usd: float
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    max_drawdown_usd: float
    win_rate_pct: float
    profit_factor: float
    expectancy_r: float
    avg_win_usd: float
    avg_loss_usd: float
    avg_r_multiple: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    max_consecutive_losses: int
    max_consecutive_wins: int
    liquidations_count: int
    total_fees_usd: float
    total_funding_usd: float
    long_trades_count: int
    long_win_rate_pct: float
    short_trades_count: int
    short_win_rate_pct: float


class MetricsCalculator:
    """
    Computes professional quantitative portfolio metrics on trade ledgers and equity series.
    """

    @classmethod
    def compute_metrics(
        cls,
        trades: list[dict[str, Any]],
        equity_curve: list[float],
        initial_balance: float,
        duration_days: float = 30.0,
    ) -> BacktestMetrics:
        if not trades or not equity_curve:
            return BacktestMetrics(
                initial_balance=initial_balance,
                final_equity=initial_balance,
                net_profit_usd=0.0,
                total_return_pct=0.0,
                cagr_pct=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                calmar_ratio=0.0,
                max_drawdown_pct=0.0,
                max_drawdown_usd=0.0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                expectancy_r=0.0,
                avg_win_usd=0.0,
                avg_loss_usd=0.0,
                avg_r_multiple=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                max_consecutive_losses=0,
                max_consecutive_wins=0,
                liquidations_count=0,
                total_fees_usd=0.0,
                total_funding_usd=0.0,
                long_trades_count=0,
                long_win_rate_pct=0.0,
                short_trades_count=0,
                short_win_rate_pct=0.0,
            )

        final_eq = equity_curve[-1]
        net_profit = final_eq - initial_balance
        tot_ret_pct = (net_profit / initial_balance) * 100.0

        # CAGR
        years = max(0.01, duration_days / 365.25)
        cagr = ((final_eq / initial_balance) ** (1.0 / years) - 1.0) * 100.0 if final_eq > 0 else -100.0

        # Max Drawdown calculation
        eq_series = pd.Series(equity_curve)
        peak = eq_series.cummax()
        drawdown_usd = peak - eq_series
        drawdown_pct = (drawdown_usd / peak.replace(0, 1.0)) * 100.0
        max_dd_pct = float(drawdown_pct.max())
        max_dd_usd = float(drawdown_usd.max())

        # Trade analytics
        wins = [t for t in trades if t.get("net_pnl", 0) > 0]
        losses = [t for t in trades if t.get("net_pnl", 0) <= 0]
        liqs = [t for t in trades if t.get("is_liquidation", False)]

        win_count = len(wins)
        loss_count = len(losses)
        tot_trades = len(trades)
        win_rate = (win_count / max(1, tot_trades)) * 100.0

        gross_win = sum(t["net_pnl"] for t in wins)
        gross_loss = abs(sum(t["net_pnl"] for t in losses))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

        avg_win = gross_win / win_count if win_count > 0 else 0.0
        avg_loss = gross_loss / loss_count if loss_count > 0 else 0.0

        # Expectancy in R
        r_multiples = [t.get("realized_r", 0.0) for t in trades]
        avg_r = float(np.mean(r_multiples)) if r_multiples else 0.0
        expectancy_r = (win_rate / 100.0 * (avg_win / avg_loss if avg_loss > 0 else 1.5)) - ((100.0 - win_rate) / 100.0 * 1.0)

        # Sharpe & Sortino based on trade returns
        trade_returns = [t["net_pnl"] / initial_balance for t in trades]
        ret_mean = np.mean(trade_returns) if trade_returns else 0.0
        ret_std = np.std(trade_returns) if trade_returns else 1.0
        sharpe = (ret_mean / ret_std * np.sqrt(365)) if ret_std > 1e-6 else 0.0

        downside_returns = [r for r in trade_returns if r < 0]
        downside_std = np.std(downside_returns) if downside_returns else 1.0
        sortino = (ret_mean / downside_std * np.sqrt(365)) if downside_std > 1e-6 else 0.0

        calmar = (tot_ret_pct / max_dd_pct) if max_dd_pct > 0 else 0.0

        # Consecutive streaks
        max_c_win = 0
        max_c_loss = 0
        cur_c_win = 0
        cur_c_loss = 0
        for t in trades:
            if t.get("net_pnl", 0) > 0:
                cur_c_win += 1
                cur_c_loss = 0
                max_c_win = max(max_c_win, cur_c_win)
            else:
                cur_c_loss += 1
                cur_c_win = 0
                max_c_loss = max(max_c_loss, cur_c_loss)

        # Long vs Short
        longs = [t for t in trades if t.get("side") == "LONG"]
        shorts = [t for t in trades if t.get("side") == "SHORT"]
        long_wr = (sum(1 for t in longs if t.get("net_pnl", 0) > 0) / max(1, len(longs))) * 100.0
        short_wr = (sum(1 for t in shorts if t.get("net_pnl", 0) > 0) / max(1, len(shorts))) * 100.0

        tot_fees = sum(t.get("fee", 0) for t in trades)
        tot_funding = sum(t.get("funding", 0) for t in trades)

        return BacktestMetrics(
            initial_balance=round(initial_balance, 2),
            final_equity=round(final_eq, 2),
            net_profit_usd=round(net_profit, 2),
            total_return_pct=round(tot_ret_pct, 2),
            cagr_pct=round(cagr, 2),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            calmar_ratio=round(calmar, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            max_drawdown_usd=round(max_dd_usd, 2),
            win_rate_pct=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            expectancy_r=round(expectancy_r, 2),
            avg_win_usd=round(avg_win, 2),
            avg_loss_usd=round(avg_loss, 2),
            avg_r_multiple=round(avg_r, 2),
            total_trades=tot_trades,
            winning_trades=win_count,
            losing_trades=loss_count,
            max_consecutive_losses=max_c_loss,
            max_consecutive_wins=max_c_win,
            liquidations_count=len(liqs),
            total_fees_usd=round(tot_fees, 2),
            total_funding_usd=round(tot_funding, 2),
            long_trades_count=len(longs),
            long_win_rate_pct=round(long_wr, 1),
            short_trades_count=len(shorts),
            short_win_rate_pct=round(short_wr, 1),
        )
