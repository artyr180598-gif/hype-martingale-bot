"""FastAPI wrapper for v3 -- used by ``python -m v3 serve``.

Endpoints:
  * GET /health                     -- system + data mode health;
  * GET /api/v3/signal/{symbol}     -- one signal snapshot;
  * POST /api/v3/scan               -- scan universe and persist snapshots;
  * GET  /api/v3/backtest/{symbol}  -- historical walk-forward test;
  * GET  /api/v3/history/{symbol}   -- recent persisted signals.

The API is read-only and does not execute orders (execution engine is separate).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import FastAPI, Query

from src.core.timeutil import now_ms
from v3.backtest import run_backtest
from v3.config import SignalConfig
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.models import TradingSignal
from v3.observability import metrics
from v3.store import SignalLifecycle, SignalStore


class V3Runtime:
    def __init__(self) -> None:
        self.cfg = SignalConfig()
        self.data = FuturesDataService(cfg=self.cfg)
        self.engine = FuturesSignalEngine(self.data, self.cfg)
        self.store = SignalStore(self.cfg.db_path)
        self.lifecycle = SignalLifecycle(self.store, self.cfg.COOLDOWN_SECONDS, self.cfg.MAX_ACTIVE_SIGNALS)
        self.mode = "unknown"
        self.started = False

    async def start(self) -> None:
        if self.started:
            return
        try:
            self.mode = await self.data.probe()
            metrics.mark_mode(self.mode, True)
        except Exception as exc:  # noqa: BLE001
            self.mode = f"error:{exc}"
            metrics.mark_mode("error", False)
            metrics.record_error("api.startup", exc)
        self.started = True

    async def stop(self) -> None:
        await self.data.close()


app = FastAPI(title="HYPE v3 Futures Signal Intelligence", version="3.0.0")
runtime = V3Runtime()


@app.on_event("startup")
async def _startup() -> None:
    await runtime.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await runtime.stop()


@app.get("/health")
async def health() -> dict[str, Any]:
    snap = metrics.snapshot(
        db_ok=True,
        scanner_ok=runtime.store.get_state("v3_last_scan_ms", "") != "",
        active_signals=len(runtime.lifecycle.active()),
        signals_saved=len(runtime.store.recent_signals(limit=10_000)),
        outcomes=len(runtime.store.outcomes()),
    )
    return {
        "status": "ok" if runtime.started else "starting",
        "app": "FuturesSignalIntelligence",
        "version": runtime.cfg.APP_VERSION,
        "mode": runtime.mode,
        "db": str(runtime.cfg.db_path),
        "signals": snap.signals_saved,
        "health": snap.to_dict(),
    }


@app.get("/api/v3/signal/{symbol}")
async def signal(symbol: str, refresh: bool = True) -> dict[str, Any]:
    sig = await runtime.engine.analyze(symbol.upper(), refresh=refresh)
    runtime.store.save_signal(sig)
    return sig.to_dict()


@app.post("/api/v3/scan")
async def scan(limit: int = Query(100, ge=1, le=500), top: int = Query(10, ge=1, le=50)) -> dict[str, Any]:
    from v3.scanner import Scanner

    tickers = await runtime.data.tickers()
    scanner = Scanner(runtime.engine, runtime.cfg)
    result = await scanner.run(tickers, limit=limit, top=top)
    for item in result.analyzed:
        runtime.store.save_signal(item["signal"])
    now = str(int(time.time() * 1000))
    runtime.store.set_state("last_scan_ms", now)
    runtime.store.set_state("v3_last_scan_ms", now)
    metrics.record_scan()
    return {
        **scanner.to_dict(),
        "mode": runtime.mode,
        "ts_ms": now_ms(),
        "tradable": [item["signal"].to_dict() for item in result.analyzed if item["signal"].direction in ("LONG", "SHORT")][:top],
    }


@app.get("/api/v3/backtest/{symbol}")
async def backtest(
    symbol: str,
    tf: str = Query("15m"),
    bars: int = Query(1000, ge=300, le=5000),
    warmup: int = Query(120, ge=50, le=500),
) -> dict[str, Any]:
    history = await runtime.data.history(symbol.upper(), tf, bars)
    res = run_backtest(
        runtime.engine, symbol, history,
        entry_tf=tf,
        medium_tf={"1m": "15m", "5m": "15m", "15m": "1h", "30m": "2h", "1h": "4h", "4h": "1d"}.get(tf, "1h"),
        macro_tf={"1m": "4h", "5m": "4h", "15m": "4h", "30m": "4h", "1h": "1d", "4h": "1d"}.get(tf, "4h"),
        warmup=warmup,
        cfg=runtime.cfg,
    )
    return res.to_dict()


@app.get("/api/v3/history/{symbol}")
async def history(symbol: str, limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    return {"symbol": symbol.upper(), "signals": runtime.store.recent_signals(symbol, limit)}


@app.get("/api/v3/status")
async def status() -> dict[str, Any]:
    snap = metrics.snapshot(
        db_ok=True,
        scanner_ok=runtime.store.get_state("v3_last_scan_ms", "") != "",
        active_signals=len(runtime.lifecycle.active()),
        signals_saved=len(runtime.store.recent_signals(limit=10_000)),
        outcomes=len(runtime.store.outcomes()),
    )
    return {
        "mode": runtime.mode,
        "started": runtime.started,
        "last_cycle_ms": runtime.store.get_state("v3_last_cycle_ms", "0"),
        "signal_count": snap.signals_saved,
        "outcome_count": snap.outcomes,
        "active": snap.active_signals,
        "recent": [r for r in runtime.store.recent_signals(limit=10)],
        "health": snap.to_dict(),
        "errors": metrics.recent_errors(limit=10),
    }


@app.post("/api/v3/track")
async def track(prices: dict[str, float]) -> dict[str, Any]:
    """Provide a price map {SYMBOL: price}; returns TP/SL lifecycle events."""
    events = runtime.lifecycle.track_prices(prices)
    return {"events": events}


@app.get("/api/v3/explain/{uid}")
async def explain(uid: str) -> dict[str, Any]:
    """Admin/debug: why did this signal score what it scored?"""
    row = runtime.store.get_signal(uid)
    if not row:
        return {"found": False, "uid": uid}
    payload = row.get("payload", {})
    return {
        "found": True,
        "uid": uid,
        "symbol": row.get("symbol"),
        "direction": row.get("direction"),
        "status": row.get("status"),
        "score_breakdown": payload.get("score_breakdown", {}),
        "risk_brief": payload.get("risk_brief", {}),
        "no_trade_reasons": payload.get("no_trade_reasons", []),
        "reasons": payload.get("reasons", []),
        "risks": payload.get("risks", []),
    }


@app.get("/api/v3/outcomes")
async def outcomes(symbol: str | None = None) -> dict[str, Any]:
    return {"outcomes": runtime.store.outcomes(symbol)}


@app.get("/api/v3/walk-forward/{symbol}")
async def walk_forward(
    symbol: str,
    tf: str = Query("15m"),
    bars: int = Query(5000, ge=2000, le=20000),
    folds: int = Query(5, ge=1, le=10),
    train: int = Query(600, ge=100, le=5000),
    test: int = Query(300, ge=50, le=5000),
    step: int = Query(300, ge=50, le=5000),
    warmup: int = Query(120, ge=50, le=500),
) -> dict[str, Any]:
    from v3.walkforward import WalkForwardConfig, walk_forward

    history = await runtime.data.history(symbol.upper(), tf, bars)
    wf = WalkForwardConfig(
        train_bars=train, test_bars=test, step_bars=step, n_folds=folds,
        warmup_bars=warmup, entry_tf=tf,
        medium_tf={"1m": "15m", "5m": "15m", "15m": "1h", "30m": "2h", "1h": "4h", "4h": "1d"}.get(tf, "1h"),
        macro_tf={"1m": "4h", "5m": "4h", "15m": "4h", "30m": "4h", "1h": "1d", "4h": "1d"}.get(tf, "4h"),
    )
    return walk_forward(runtime.engine, symbol, history, runtime.cfg, wf).to_dict()
