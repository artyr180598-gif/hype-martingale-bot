"""Per-fill trade simulation for the v3 backtester.

This is the execution model shared by backtest and walk-forward: taker fee +
slippage on every fill, pessimistic stop first inside a bar, partial exits,
gap-through-stop == loss, and no look-ahead. The unified engine deliberately
keeps this small and self-contained (no dependency on the legacy v1 analysis
engine).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Веса частичных выходов (50% / 30% / 20%).
TRANCHE_WEIGHTS = (0.5, 0.3, 0.2)


@dataclass
class BacktestConfig:
    entry_tf: str = "1h"
    medium_tf: str = "4h"
    macro_tf: str = "1d"
    warmup_bars: int = 200
    step: int = 1
    max_hold_bars: int = 96
    limit_wait_bars: int = 24
    fee_rate: float = 0.00055
    slippage_pct: float = 0.02
    min_rr: float = 1.8
    max_cost_r: float = 0.15
    min_stop_pct: float = 0.6
    cooldown_bars: int = 12
    min_score: float = 0.0
    min_confidence: float = 0.45
    allow_short: bool = True
    staged_exits: bool = True
    trail_after_t1: bool = False
    trail_atr_mult: float = 3.5
    atr_period: int = 14
    one_trade_at_a_time: bool = True

    def to_dict(self) -> dict:
        return {
            "entry_tf": self.entry_tf,
            "medium_tf": self.medium_tf,
            "macro_tf": self.macro_tf,
            "warmup_bars": self.warmup_bars,
            "step": self.step,
            "max_hold_bars": self.max_hold_bars,
            "limit_wait_bars": self.limit_wait_bars,
            "fee_rate": self.fee_rate,
            "slippage_pct": self.slippage_pct,
            "min_rr": self.min_rr,
            "cooldown_bars": self.cooldown_bars,
            "max_cost_r": self.max_cost_r,
            "min_stop_pct": self.min_stop_pct,
            "trail_after_t1": self.trail_after_t1,
            "trail_atr_mult": self.trail_atr_mult,
            "min_confidence": self.min_confidence,
            "allow_short": self.allow_short,
            "staged_exits": self.staged_exits,
        }


@dataclass
class Trade:
    symbol: str
    direction: str
    signal_ts: int
    entry_ts: int
    entry_price: float
    stop: float
    targets: list[float]
    exit_ts: int
    exit_price: float
    r_multiple: float
    pnl_pct: float
    bars_held: int
    exit_reason: str
    score: float
    confidence: float
    tranches: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "signal_ts": self.signal_ts,
            "entry_ts": self.entry_ts,
            "entry_price": self.entry_price,
            "stop": self.stop,
            "targets": self.targets,
            "exit_ts": self.exit_ts,
            "exit_price": self.exit_price,
            "r_multiple": round(self.r_multiple, 3),
            "pnl_pct": round(self.pnl_pct, 3),
            "bars_held": self.bars_held,
            "exit_reason": self.exit_reason,
            "score": round(self.score, 1),
            "confidence": round(self.confidence, 2),
        }


def atr_series(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """True Range with a Wilder smoothing; value at bar i depends on bars <= i."""
    hi = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    cl = df["close"].to_numpy(dtype=float)
    prev = np.concatenate(([cl[0]], cl[:-1]))
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - prev), np.abs(lo - prev)))
    out = np.empty_like(tr)
    if len(tr) == 0:
        return out
    out[0] = tr[0]
    alpha = 1.0 / max(period, 1)
    for i in range(1, len(tr)):
        out[i] = alpha * tr[i] + (1 - alpha) * out[i - 1]
    return out


def cost_in_r(entry: float, stop: float, cfg: "BacktestConfig") -> float:
    """Cost in R of opening+closing: fees both sides plus slippage on fills."""
    risk = abs(entry - stop)
    if risk <= 0:
        return float("inf")
    return (2 * cfg.fee_rate + 2 * cfg.slippage_pct / 100.0) * entry / risk


def _tranche_r(weight: float, entry: float, exit_price: float, risk: float, sign: float, fee: float) -> float:
    gross = sign * (exit_price - entry) / risk
    fee_r = fee * (entry + exit_price) / risk
    return weight * (gross - fee_r)


def simulate_trade(
    symbol: str,
    direction: str,
    plan,
    future: pd.DataFrame,
    signal_idx: int,
    signal_ts: int,
    cfg: BacktestConfig,
    score: float = 0.0,
    confidence: float = 0.0,
) -> Trade | None:
    """Simulate one fill from the signal bar onwards with no look-ahead."""
    is_long = direction == "LONG"
    slip = cfg.slippage_pct / 100.0
    lo_zone, hi_zone = plan.entry_zone
    targets = list(plan.targets)
    stop0 = plan.stop_loss

    # ── Entry limit fill ────────────────────────────────────────
    entry_idx = None
    entry_price = None
    start = signal_idx + 1
    for j in range(start, min(start + cfg.limit_wait_bars + 1, len(future))):
        bar = future.iloc[j]
        if is_long:
            if bar["low"] <= hi_zone:
                entry_idx = j
                fill = min(hi_zone, bar["open"])
                entry_price = fill * (1 - slip)
                break
        else:
            if bar["high"] >= lo_zone:
                entry_idx = j
                fill = max(lo_zone, bar["open"])
                entry_price = fill * (1 + slip)
                break
    if entry_idx is None or entry_price is None or entry_price <= 0:
        return None

    # Gap through stop.
    crossed = (entry_price <= stop0) if is_long else (entry_price >= stop0)
    if crossed:
        fee_r = cfg.fee_rate * 2 * entry_price / abs(entry_price - stop0)
        gap_sign = 1.0 if is_long else -1.0
        loss = gap_sign * (stop0 - entry_price) / abs(entry_price - stop0)
        return Trade(
            symbol=symbol, direction=direction, signal_ts=signal_ts,
            entry_ts=int(future.iloc[entry_idx]["ts"]), entry_price=entry_price,
            stop=stop0, targets=targets, exit_ts=int(future.iloc[entry_idx]["ts"]),
            exit_price=entry_price, r_multiple=-(abs(loss) + fee_r),
            pnl_pct=0.0, bars_held=0, exit_reason="gap_stop",
            score=score, confidence=confidence,
            tranches=[{"weight": 1.0, "price": entry_price, "reason": "gap_stop"}],
        )

    risk = abs(entry_price - stop0)
    if risk <= 0:
        return None

    sign = 1.0 if is_long else -1.0
    stop = stop0
    remaining = 1.0
    r_total = 0.0
    tranches: list[dict] = []
    exit_idx = None
    exit_reason = "timeout"
    exit_price = None
    be_moved = False
    trailed = False

    weights = TRANCHE_WEIGHTS if (cfg.staged_exits and len(targets) >= 3) else (1.0,)
    active_targets = targets[: len(weights)]
    done = [False] * len(active_targets)
    atr = atr_series(future, cfg.atr_period)
    highest = float(future.iloc[entry_idx]["high"])
    lowest = float(future.iloc[entry_idx]["low"])

    for j in range(entry_idx, min(entry_idx + cfg.max_hold_bars + 1, len(future))):
        bar = future.iloc[j]
        hi, lo = float(bar["high"]), float(bar["low"])

        # Trailing uses only closed bars (values up to j-1).
        if be_moved and cfg.trail_after_t1 and j > entry_idx:
            a = float(atr[j - 1])
            if a > 0:
                trail = (highest - cfg.trail_atr_mult * a) if is_long else \
                    (lowest + cfg.trail_atr_mult * a)
                better = (trail > stop) if is_long else (trail < stop)
                if better:
                    stop = trail
                    trailed = True

        # Stop first (pessimistic worst case inside a bar).
        stopped = (lo <= stop) if is_long else (hi >= stop)
        if stopped and remaining > 0:
            fill = stop * (1 - slip) if is_long else stop * (1 + slip)
            r_total += _tranche_r(remaining, entry_price, fill, risk, sign, cfg.fee_rate)
            tranches.append({"weight": remaining, "price": fill, "reason": "stop"})
            if not be_moved:
                exit_reason = "stop_loss"
            elif trailed:
                exit_reason = "trailing"
            else:
                exit_reason = "breakeven"
            exit_idx, exit_price = j, fill
            remaining = 0.0
            break

        # Targets in order; each executes once.
        for k, tgt in enumerate(active_targets):
            if done[k]:
                continue
            hit = (hi >= tgt) if is_long else (lo <= tgt)
            if not hit:
                continue
            w = weights[k]
            fill = tgt * (1 - slip) if is_long else tgt * (1 + slip)
            r_total += _tranche_r(w, entry_price, fill, risk, sign, cfg.fee_rate)
            tranches.append({"weight": w, "price": fill, "reason": f"target_{k + 1}"})
            remaining -= w
            done[k] = True
            if k == 0 and cfg.staged_exits:
                stop = entry_price
                be_moved = True
            if remaining <= 1e-9:
                exit_idx, exit_price, exit_reason = j, fill, "target"
                break
        if remaining <= 1e-9:
            break

        highest = max(highest, hi)
        lowest = min(lowest, lo)

    if remaining > 1e-9:
        last_idx = min(entry_idx + cfg.max_hold_bars, len(future) - 1)
        fill = float(future.iloc[last_idx]["close"])
        fill = fill * (1 - slip) if is_long else fill * (1 + slip)
        r_total += _tranche_r(remaining, entry_price, fill, risk, sign, cfg.fee_rate)
        tranches.append({"weight": remaining, "price": fill, "reason": "timeout"})
        exit_idx, exit_price, exit_reason = last_idx, fill, "timeout"

    if exit_price is None:
        return None

    pnl_pct = r_total * (risk / entry_price * 100.0)
    return Trade(
        symbol=symbol,
        direction=direction,
        signal_ts=signal_ts,
        entry_ts=int(future.iloc[entry_idx]["ts"]),
        entry_price=entry_price,
        stop=stop0,
        targets=targets,
        exit_ts=int(future.iloc[exit_idx]["ts"]),
        exit_price=exit_price,
        r_multiple=r_total,
        pnl_pct=pnl_pct,
        bars_held=exit_idx - entry_idx,
        exit_reason=exit_reason,
        score=score,
        confidence=confidence,
        tranches=tranches,
    )
