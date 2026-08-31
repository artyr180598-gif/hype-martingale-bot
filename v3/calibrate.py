"""Read-only threshold calibration on a live/backtest sample.

This module does **not** edit ``SignalConfig``. It runs the same deterministic
``run_backtest`` on a sample of symbols/timeframe, aggregates the outcome
distribution and prints suggestions an operator can apply to ``.env``.  It is a
report tool, never a magic "tune until green" step: any threshold change must be
re-validated with ``walk_forward``.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from v3.backtest import BacktestResult, run_backtest
from v3.config import SignalConfig
from v3.engine import FuturesSignalEngine


@dataclass
class CalibrationRow:
    symbol: str
    tf: str
    bars: int
    signals: int
    trades: int
    win_rate: float
    profit_factor: float
    expectancy_r: float
    max_consecutive_losses: int
    avg_quality: float
    avg_confidence: float
    avg_rr: float
    tier_distribution: dict[str, int] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CalibrationReport:
    mode: str
    tf: str
    bars: int
    rows: list[CalibrationRow] = field(default_factory=list)
    aggregate: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tf": self.tf,
            "bars": self.bars,
            "rows": [r.to_dict() for r in self.rows],
            "aggregate": self.aggregate,
            "suggestions": self.suggestions,
            "duration_sec": self.duration_sec,
            "error": self.error,
        }


_TIER_RANKS = {"S": 0, "A": 1, "B": 2, "C": 3}


def _aggregate(rows: list[CalibrationRow]) -> dict[str, Any]:
    if not rows:
        return {"symbols": 0, "signals": 0, "trades": 0, "win_rate": 0.0, "expectancy_r": 0.0}
    signals = sum(r.signals for r in rows)
    trades = sum(r.trades for r in rows)
    wins = sum(int(r.trades * r.win_rate / 100.0) for r in rows)
    agg = {
        "symbols": len(rows),
        "signals": signals,
        "trades": trades,
        "win_rate": round(wins / trades * 100.0, 2) if trades else 0.0,
        "expectancy_r": round(sum(r.expectancy_r * r.trades for r in rows) / trades, 4) if trades else 0.0,
        "max_consecutive_losses": max((r.max_consecutive_losses for r in rows), default=0),
        "avg_quality": round(sum(r.avg_quality * r.signals for r in rows) / signals, 2) if signals else 0.0,
        "avg_confidence": round(sum(r.avg_confidence * r.signals for r in rows) / signals, 3) if signals else 0.0,
        "avg_rr": round(sum(r.avg_rr * r.trades for r in rows) / trades, 2) if trades else 0.0,
    }
    return agg


def _suggestions(rows: list[CalibrationRow], agg: dict[str, Any], cfg: SignalConfig) -> list[str]:
    out: list[str] = []
    if not rows or agg["trades"] == 0:
        out.append("No trades in the sample — signal quality can not be calibrated. Do not lower criteria just to get trades.")
        return out

    if agg["win_rate"] < 45 and agg["expectancy_r"] <= 0:
        out.append(f"Sample win-rate {agg['win_rate']:.1f}% with non-positive expectancy; consider raising QUALITY_MIN/CONFIDENCE_MIN/SCAN_TOP or increasing MIN_RISK_REWARD.")
    if agg["max_consecutive_losses"] >= 5:
        out.append(f"Sample has {agg['max_consecutive_losses']} consecutive losses; keep COOLDOWN_SECONDS high and never size up after losses.")
    if agg["avg_confidence"] < cfg.CONFIDENCE_MIN:
        out.append(f"Average confidence {agg['avg_confidence']:.2f} is below CONFIDENCE_MIN={cfg.CONFIDENCE_MIN:.2f}; the gate is likely too loose for this sample.")
    if agg["avg_rr"] > 0 and agg["expectancy_r"] <= 0:
        out.append("Positive average R:R yet non-positive expectancy — the win-rate/risk balance is the problem, not the R:R.")
    if agg["win_rate"] >= 50 and agg["expectancy_r"] > 0:
        out.append("Sample is positive; verify with walk_forward before treating it as an edge. Never guarantee future results.")

    # specific tier feedback (worst tier with any trades)
    present = {t for r in rows for t in r.tier_distribution if r.tier_distribution[t] > 0}
    if present:
        worst = max(present, key=lambda t: _TIER_RANKS.get(t, 99))
        out.append(f"Worst tier present in sample is {worst}; check its trade count and expectancy before allowing it through live.")
    return out


async def calibrate(
    engine: FuturesSignalEngine,
    symbols: list[str],
    tf: str = "15m",
    bars: int = 2000,
    warmup: int = 120,
    cfg: SignalConfig | None = None,
    max_errors: int = 2,
) -> CalibrationReport:
    """Run one in-sample backtest per symbol and return a calibration report."""
    cfg = cfg or SignalConfig()
    started = time.time()
    report = CalibrationReport(mode=engine.data.mode, tf=tf, bars=bars)
    errors = 0
    for symbol in symbols:
        sym = symbol.upper()
        row = CalibrationRow(symbol=sym, tf=tf, bars=bars, signals=0, trades=0, win_rate=0.0, profit_factor=0.0, expectancy_r=0.0, max_consecutive_losses=0, avg_quality=0.0, avg_confidence=0.0, avg_rr=0.0)
        try:
            history: pd.DataFrame = await engine.data.history(sym, tf, bars)
            res: BacktestResult = run_backtest(
                engine, sym, history,
                entry_tf=tf,
                medium_tf={"1m": "15m", "5m": "15m", "15m": "1h", "30m": "2h", "1h": "4h", "4h": "1d"}.get(tf, "1h"),
                macro_tf={"1m": "4h", "5m": "4h", "15m": "4h", "30m": "4h", "1h": "1d", "4h": "1d"}.get(tf, "4h"),
                warmup=warmup,
                cfg=cfg,
            )
            row.signals = res.signals
            row.trades = len(res.trades)
            row.win_rate = round(res.metrics.get("win_rate", 0.0), 2)
            row.profit_factor = round(res.metrics.get("profit_factor", 0.0), 3)
            row.expectancy_r = round(res.metrics.get("expectancy_r", 0.0), 4)
            row.max_consecutive_losses = int(res.metrics.get("max_consecutive_losses", 0))
            qualities = [t.score for t in res.trades]
            confidences = [t.confidence for t in res.trades]
            rrs = [t.rr for t in res.trades]
            row.avg_quality = round(sum(qualities) / len(qualities), 2) if qualities else 0.0
            row.avg_confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
            row.avg_rr = round(sum(rrs) / len(rrs), 2) if rrs else 0.0
            for t in res.trades:
                tier = t.signal.get("tier", "")
                row.tier_distribution[tier] = row.tier_distribution.get(tier, 0) + 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            row.error = str(exc)
            if errors >= max_errors:
                report.error = f"stopped after {errors} symbol errors"
                break
        report.rows.append(row)

    report.aggregate = _aggregate(report.rows)
    report.suggestions = _suggestions(report.rows, report.aggregate, cfg)
    report.duration_sec = round(time.time() - started, 2)
    return report
