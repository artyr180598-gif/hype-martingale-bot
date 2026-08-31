"""Walk-forward backtester for v3 signal engine.

Uses the same ``evaluate_bundle`` code path as live (backtest/live parity),
builds higher timeframes by resampling the entry history, and only feeds closed
bars to the engine -- no look-ahead.

Execution uses the same simulation assumptions as the v1 backtester:
  * taker fee + slippage on every fill;
  * inside a bar, stop is checked pessimistically before targets;
  * partial exits (50%/30%/20%);
  * gap through stop is a loss, never "a profitable exit".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from v3.config import SignalConfig
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.models import DataBundle, TradingSignal
from v3.simulation import BacktestConfig, simulate_trade

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000}
RESAMPLE = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1D"}


@dataclass
class BacktestTrade:
    signal: dict[str, Any]
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    direction: str
    rr: float
    r_multiple: float
    pnl_pct: float
    bars_held: int
    exit_reason: str
    score: float
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class BacktestResult:
    symbol: str
    start_ts: int = 0
    end_ts: int = 0
    bars: int = 0
    signals: int = 0
    trades: list[BacktestTrade] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    is_demo: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "bars": self.bars,
            "signals": self.signals,
            "trades": [t.to_dict() for t in self.trades],
            "metrics": self.metrics,
            "is_demo": self.is_demo,
        }


def run_backtest(
    engine: FuturesSignalEngine,
    symbol: str,
    history: pd.DataFrame,
    entry_tf: str = "15m",
    medium_tf: str = "1h",
    macro_tf: str = "4h",
    bars: int | None = None,
    warmup: int = 120,
    cfg: SignalConfig | None = None,
) -> BacktestResult:
    cfg = cfg or SignalConfig()
    df = history.copy()
    for col in ("ts", "open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    if bars:
        df = df.tail(bars).reset_index(drop=True)
    if len(df) < warmup + 30:
        return BacktestResult(symbol=symbol.upper(), metrics={"error": "not enough history"})

    med = _resample(df, medium_tf)
    macro = _resample(df, macro_tf)
    bt_cfg = BacktestConfig(
        entry_tf=entry_tf,
        medium_tf=medium_tf,
        macro_tf=macro_tf,
        warmup_bars=warmup,
        fee_rate=0.00055,
        slippage_pct=0.02,
        min_rr=cfg.MIN_RISK_REWARD,
        min_confidence=cfg.CONFIDENCE_MIN,
        min_score=cfg.C_TIER_MIN,
        staged_exits=True,
        one_trade_at_a_time=True,
        max_hold_bars=200,
        limit_wait_bars=cfg.MAX_ENTRY_DISTANCE_ATR and 24,
    )

    trades: list[BacktestTrade] = []
    signals = 0
    tradable_signals = 0
    i = warmup
    result = BacktestResult(symbol=symbol.upper(), start_ts=int(df["ts"].iloc[0]), end_ts=int(df["ts"].iloc[-1]), bars=len(df), is_demo=engine.data.is_demo)

    while i < len(df) - 3:
        current_bar = df.iloc[i]
        now_ts = int(current_bar["ts"])
        close = float(current_bar["close"])
        tf_map = {"entry": df.iloc[: i + 1].reset_index(drop=True)}
        tf_map[entry_tf] = df.iloc[: i + 1].reset_index(drop=True)
        if med is not None:
            tf_map[medium_tf] = _closed_upto(med, now_ts + TF_MS[entry_tf], medium_tf)
        if macro is not None:
            tf_map[macro_tf] = _closed_upto(macro, now_ts + TF_MS[entry_tf], macro_tf)
        _cfg_tfs = [entry_tf, medium_tf, macro_tf]
        tf_map = {k: v for k, v in tf_map.items() if k in _cfg_tfs and v is not None and len(v) >= 40}
        if len(tf_map) < 2:
            i += 1
            continue

        turnover = float((df.iloc[max(0, i - 95) : i + 1]["close"] * df.iloc[max(0, i - 95) : i + 1]["volume"]).sum())
        volume_24h = float(df.iloc[max(0, i - 95) : i + 1]["volume"].sum())
        bundle = DataBundle(
            symbol=symbol.upper(),
            ts_ms=now_ts,
            price=close,
            price_24h_pct=0.0,
            turnover_24h=turnover or 1e9,
            volume_24h=volume_24h,
            is_demo=engine.data.is_demo,
            degraded=["funding unavailable", "order book unavailable", "global context unavailable"],
        )
        try:
            signal = engine.evaluate_bundle(bundle, tf_map, btc_tf=None, strict_liquidity=False)
        except Exception:  # noqa: BLE001
            i += 1
            continue
        signals += 1
        if signal.direction not in ("LONG", "SHORT"):
            i += 1
            continue
        tradable_signals += 1

        v1_plan = _as_v1_plan(signal)
        sim = simulate_trade(symbol.upper(), signal.direction, v1_plan, df, i, now_ts, bt_cfg, signal.score, signal.confidence)
        if sim is None:
            i += 1
            continue

        # Model conservative funding cost when the trade is held across
        # funding intervals. We do not have a live funding series per bar, so we
        # use the configured per-8h rate against the position (worst case for
        # both sides). This keeps backtest honest about carry cost.
        funding_rate = getattr(cfg, "BACKTEST_FUNDING_RATE", 0.0)
        if sim.bars_held > 0 and funding_rate > 0 and TF_MS.get(entry_tf):
            intervals_per_8h = (8 * 3600 * 1000) / TF_MS[entry_tf]
            hold_intervals = sim.bars_held / max(intervals_per_8h, 1.0)
            charge_pct = hold_intervals * funding_rate * 100.0
            risk_price = abs(sim.entry_price - signal.stop_loss)
            risk_pct = (risk_price / sim.entry_price * 100.0) if risk_price and sim.entry_price else 0.0
            sim.r_multiple -= (charge_pct / risk_pct) if risk_pct > 0 else 0.0
            sim.pnl_pct -= charge_pct

        trades.append(
            BacktestTrade(
                signal=signal.to_dict(),
                entry_ts=sim.entry_ts,
                exit_ts=sim.exit_ts,
                entry_price=sim.entry_price,
                exit_price=sim.exit_price,
                direction=signal.direction,
                rr=signal.rr,
                r_multiple=sim.r_multiple,
                pnl_pct=sim.pnl_pct,
                bars_held=sim.bars_held,
                exit_reason=sim.exit_reason,
                score=sim.score,
                confidence=sim.confidence,
            )
        )
        # move to the exit/candidate bar to avoid reopening the same setup
        exit_idx = _index_of_ts(df, sim.exit_ts)
        i = exit_idx if exit_idx is not None else min(len(df) - 1, i + max(1, sim.bars_held))
        i += 1

    result.trades = trades
    result.signals = signals
    result.metrics = metrics_from_trades(trades, signals_generated=tradable_signals)
    return result


def _resample(df: pd.DataFrame, target_tf: str) -> pd.DataFrame | None:
    rule = RESAMPLE.get(target_tf)
    if rule is None or target_tf not in TF_MS:
        return None
    idx = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    d = df.set_index(idx)
    out = (
        d.resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    out["ts"] = (out.index.view("int64") // 1_000_000).astype("int64")
    return out.reset_index(drop=True)[["ts", "open", "high", "low", "close", "volume"]]


def _closed_upto(df: pd.DataFrame, decision_ts: int, tf: str) -> pd.DataFrame:
    """Bars of `tf` that are already closed at `decision_ts`."""
    if df.empty:
        return df
    limit = decision_ts
    mask = (df["ts"].astype("int64") + TF_MS.get(tf, 3_600_000)) <= limit
    return df[mask]


def _index_of_ts(df: pd.DataFrame, ts: int) -> int | None:
    vals = df["ts"].to_numpy()
    idx = np.searchsorted(vals, ts)
    if idx < len(vals) and int(vals[idx]) == int(ts):
        return int(idx)
    return None


def _as_v1_plan(signal: TradingSignal):
    class Plan:
        pass

    p = Plan()
    p.entry_zone = signal.entry_zone
    p.stop_loss = signal.stop_loss
    p.targets = signal.targets
    return p


def metrics_from_trades(trades: list[BacktestTrade], signals_generated: int | None = None) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy_r": 0.0,
            "avg_win_r": 0.0, "avg_loss_r": 0.0, "max_dd_r": 0.0, "sharpe": 0.0, "sortino": 0.0,
            "max_consecutive_losses": 0, "trades_per_day": 0.0, "precision": 0.0,
            "recall": 0.0, "false_positive_rate": 0.0,
        }
    r = np.array([t.r_multiple for t in trades], dtype=float)
    wins = r[r > 0]
    losses = r[r <= 0]
    win_rate = len(wins) / len(r) * 100.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    gross_profit = float(wins.sum())
    gross_loss = -float(losses.sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    equity = [0.0] + list(np.cumsum(r))
    dd = _max_drawdown(equity)
    sharpe = float(r.mean() / r.std() * np.sqrt(len(r))) if len(r) > 1 and r.std() > 0 else 0.0
    downs = r[r < 0]
    sortino = float(r.mean() / downs.std() * np.sqrt(len(r))) if len(downs) > 1 and downs.std() > 0 else 0.0

    # consecutive losses
    max_consecutive = 0
    current = 0
    for x in r:
        if x <= 0:
            current += 1
            max_consecutive = max(max_consecutive, current)
        else:
            current = 0

    # time-based frequency (days spanned by the execution timestamps)
    ts_vals: list[int] = []
    for t in trades:
        if isinstance(t, dict):
            ts_vals.append(int(t.get("ts_ms") or t.get("entry_ts") or 0))
            continue
        sig = getattr(t, "signal", None)
        if isinstance(sig, dict):
            ts_vals.append(int(sig.get("ts_ms") or getattr(t, "entry_ts", 0)))
        elif hasattr(sig, "ts_ms"):
            ts_vals.append(int(getattr(sig, "ts_ms") or getattr(t, "entry_ts", 0)))
        else:
            ts_vals.append(int(getattr(t, "entry_ts", 0)))
    days = max(1e-9, (max(ts_vals) - min(ts_vals)) / 86_400_000) if ts_vals else 1e-9
    trades_per_day = len(r) / days if days > 0 else 0.0

    precision = len(wins) / len(r) * 100.0
    false_positive_rate = len(losses) / len(r) * 100.0
    recall = len(r) / signals_generated * 100.0 if signals_generated and signals_generated > 0 else 0.0

    return {
        "trades": len(r),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 3),
        "expectancy_r": round(float(r.mean()), 3),
        "avg_win_r": round(avg_win, 3),
        "avg_loss_r": round(avg_loss, 3),
        "avg_r": round(float(r.mean()), 3),
        "max_dd_r": round(dd, 3),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "total_r": round(float(r.sum()), 3),
        "max_consecutive_losses": max_consecutive,
        "trades_per_day": round(trades_per_day, 3),
        "precision": round(precision, 2),
        "recall": round(recall, 2),
        "false_positive_rate": round(false_positive_rate, 2),
        "signals_generated": signals_generated or 0,
        "by_direction": _direction_breakdown(trades),
        "by_regime": _regime_breakdown(trades),
    }


def _group_stats(r: np.ndarray) -> dict[str, Any]:
    if len(r) == 0:
        return {"trades": 0, "win_rate": 0.0, "expectancy_r": 0.0, "total_r": 0.0}
    wins = float((r > 0).mean() * 100.0)
    return {
        "trades": int(len(r)),
        "win_rate": round(wins, 2),
        "expectancy_r": round(float(r.mean()), 3),
        "total_r": round(float(r.sum()), 3),
    }


def _direction_breakdown(trades: list[BacktestTrade]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for t in trades:
        sig = getattr(t, "signal", None)
        direction = sig.get("direction") if isinstance(sig, dict) else None
        groups.setdefault(str(direction or "UNKNOWN"), []).append(float(t.r_multiple))
    return {k: _group_stats(np.array(v, dtype=float)) for k, v in sorted(groups.items())}


def _regime_breakdown(trades: list[BacktestTrade]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for t in trades:
        sig = getattr(t, "signal", None)
        regime = "UNKNOWN"
        if isinstance(sig, dict):
            regime = str((sig.get("features") or {}).get("regime", {}).get("regime") or "UNKNOWN")
        groups.setdefault(regime, []).append(float(t.r_multiple))
    return {k: _group_stats(np.array(v, dtype=float)) for k, v in sorted(groups.items())}


def _max_drawdown(equity: list[float]) -> float:
    peak = 0.0
    dd = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return dd
