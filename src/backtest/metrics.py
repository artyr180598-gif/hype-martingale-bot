"""
Метрики бэктеста.

Главная метрика — R (кратность начального риска), а не процент и не доллары:
она не зависит от плеча, депозита и размера позиции, поэтому результаты
разных монет и периодов сравнимы.

Ключевая честная проверка — breakeven win rate. При среднем выигрыше W и
среднем проигрыше L стратегия окупается ровно при винрейте L/(W+L). Если
фактический винрейт ниже — бот торгует в минус независимо от числа сделок.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    total_trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate: float
    total_r: float
    expectancy_r: float
    avg_win_r: float
    avg_loss_r: float
    profit_factor: float
    max_consecutive_losses: int
    max_drawdown_r: float
    avg_bars_held: float
    breakeven_win_rate: float
    edge_over_breakeven: float
    buy_hold_pct: float
    by_direction: dict
    exit_reasons: dict


def compute_metrics(result, df_entry: pd.DataFrame | None = None) -> dict:
    trades = result.trades
    cfg = result.config
    m: dict = {
        "total_trades": len(trades),
        "bars_analyzed": result.bars_analyzed,
        "signals_generated": result.signals_generated,
        "skip_reasons": dict(getattr(result, "skip_reasons", {}) or {}),
        "signals_taken": len(trades),
        "trade_frequency_pct": round(100.0 * len(trades) / max(result.bars_analyzed, 1), 2),
        "signals_passed_filters": getattr(result, "signals_passed_filters", 0),
        "signal_directions": dict(getattr(result, "signal_directions", {}) or {}),
        # Сколько дошедших до рынка ордеров реально исполнилось. Низкая доля —
        # это не баг бэктестера, а свойство советника: зона входа стоит далеко
        # от цены и лимитник не срабатывает. Вживую новичок либо не войдёт,
        # либо войдёт по рынку по другой цене — план на это не рассчитан.
        "fill_rate_pct": round(
            100.0 * len(trades) / max(getattr(result, "signals_passed_filters", 0), 1), 1
        ),
        "is_demo": result.is_demo,
    }
    if not trades:
        m.update({
            "win_rate": 0.0, "total_r": 0.0, "expectancy_r": 0.0, "profit_factor": 0.0,
            "max_drawdown_r": 0.0, "max_consecutive_losses": 0, "avg_bars_held": 0.0,
            "breakeven_win_rate": 0.0, "edge_over_breakeven": 0.0, "buy_hold_pct": 0.0,
            "verdict": "нет сделок",
        })
        return m

    r = np.array([t.r_multiple for t in trades], dtype=float)
    wins = r[r > 0.0]
    losses = r[r < 0.0]
    flats = r[r == 0.0]

    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    gross_win = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    pf = (gross_win / gross_loss) if gross_loss > 1e-12 else (float("inf") if gross_win > 0 else 0.0)

    win_rate = 100.0 * len(wins) / len(r)
    total_r = float(r.sum())
    expectancy = total_r / len(r)

    # Кривая накопленного R и максимальная просадка по ней
    cum = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate(([0.0], cum)))[:-1]
    dd = peak - cum
    max_dd_r = float(dd.max()) if len(dd) else 0.0

    # Серия подряд идущих убытков
    streak = best_streak = 0
    for x in r:
        if x < 0:
            streak += 1
            best_streak = max(best_streak, streak)
        else:
            streak = 0

    # Честный порог окупаемости по фактическим средним
    denom = avg_win + abs(avg_loss)
    be_wr = (100.0 * abs(avg_loss) / denom) if denom > 1e-12 else 0.0

    by_dir: dict = {}
    for d in ("LONG", "SHORT"):
        sub = [t for t in trades if t.direction == d]
        if not sub:
            continue
        sr = np.array([t.r_multiple for t in sub], dtype=float)
        by_dir[d] = {
            "trades": len(sub),
            "win_rate": round(100.0 * float((sr > 0).sum()) / len(sr), 1),
            "total_r": round(float(sr.sum()), 2),
            "expectancy_r": round(float(sr.mean()), 3),
        }

    reasons: dict = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    buy_hold = 0.0
    if df_entry is not None and len(df_entry) >= 2:
        lo = df_entry.loc[df_entry["ts"] >= result.start_ts, "close"]
        hi = df_entry.loc[df_entry["ts"] <= result.end_ts, "close"]
        if len(lo) and len(hi):
            first, last = float(lo.iloc[0]), float(hi.iloc[-1])
            buy_hold = round(100.0 * (last - first) / first, 2) if first else 0.0

    # Вердикт: бьёт ли бот собственную точку безубыточности
    if len(r) < 30:
        verdict = "мало данных"
    elif expectancy > 0.05 and pf >= 1.2 and win_rate >= be_wr - 1.0:
        verdict = "есть эдж"
    elif expectancy > 0:
        verdict = "на грани"
    else:
        verdict = "убыточно"

    m.update({
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "breakevens": int(len(flats)),
        "win_rate": round(win_rate, 1),
        "total_r": round(total_r, 2),
        "expectancy_r": round(expectancy, 3),
        "avg_win_r": round(avg_win, 3),
        "avg_loss_r": round(avg_loss, 3),
        "profit_factor": (round(pf, 2) if np.isfinite(pf) else None),
        "max_consecutive_losses": int(best_streak),
        "max_drawdown_r": round(max_dd_r, 2),
        "avg_bars_held": round(float(np.mean([t.bars_held for t in trades])), 1),
        "avg_rr_plan": round(float(np.mean([
            _rr_of(t) for t in trades
        ])), 2),
        "median_stop_dist_pct": round(float(np.median([_stop_dist_pct(t) for t in trades])), 2),
        "gap_stops": sum(1 for t in trades if t.exit_reason == "gap_stop"),
        "breakeven_win_rate": round(be_wr, 1),
        "edge_over_breakeven": round(win_rate - be_wr, 1),
        "buy_hold_pct": buy_hold,
        "by_direction": by_dir,
        "exit_reasons": reasons,
        "best_trade_r": round(float(r.max()), 2),
        "worst_trade_r": round(float(r.min()), 2),
        "fee_drag_r": round(float(sum(
            cfg.fee_rate * (t.entry_price + sum(x["price"] * x["weight"] for x in t.tranches))
            / abs(t.entry_price - t.stop) for t in trades
        )), 2),
        "verdict": verdict,
    })
    return m


def _stop_dist_pct(t) -> float:
    """Стоп в % от цены входа: слишком узкий стоп выбивает шумом."""
    if t.entry_price <= 0:
        return 0.0
    return 100.0 * abs(t.entry_price - t.stop) / t.entry_price


def _rr_of(t) -> float:
    """Запланированный R:R до первой цели."""
    if not t.targets or abs(t.entry_price - t.stop) < 1e-12:
        return 0.0
    risk = abs(t.entry_price - t.stop)
    if t.direction == "LONG":
        return (t.targets[0] - t.entry_price) / risk
    return (t.entry_price - t.targets[0]) / risk
