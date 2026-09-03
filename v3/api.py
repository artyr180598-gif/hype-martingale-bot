"""FastAPI wrapper for v3 -- used by ``python -m v3 serve``.

Endpoints:
  * GET /health                     -- system + data mode health;
  * GET /api/v3/signal/{symbol}     -- one signal snapshot;
  * POST /api/v3/scan               -- scan universe and persist snapshots;
  * GET  /api/v3/backtest/{symbol}  -- historical walk-forward test;
  * GET  /api/v3/history/{symbol}   -- recent persisted signals;
  * GET  /api/v3/alerts             -- auto-signal state + thresholds.

The API is read-only and does not execute orders (execution engine is separate).
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from src.core.timeutil import now_ms
from v3.backtest import run_backtest
from v3.config import APP_VERSION_DEFAULT, SignalConfig
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


runtime = V3Runtime()


def require_api_token(x_api_token: str = Header(default="")) -> None:
    """Guard heavy/mutating endpoints when V3_API_TOKEN is set (local default: off)."""
    token = runtime.cfg.V3_API_TOKEN
    if token and x_api_token != token:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Token")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(
    title="HYPE v3 Futures Signal Intelligence",
    version=APP_VERSION_DEFAULT,  # раньше здесь была зашита старая версия 3.1.0
    lifespan=_lifespan,
)


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


def _payload(sig: TradingSignal) -> dict[str, Any]:
    """Сигнал для API: штатный payload + «уверенность бота» отдельным полем.

    Разбор лежит и в ``features.bot_confidence`` (его пишет движок), но
    отдельное поле удобнее клиентам: не нужно знать структуру features.
    """
    from v3.analysis.confidence import assess_confidence

    data = sig.to_dict()
    data["bot_confidence"] = assess_confidence(sig, runtime.cfg).to_dict()
    return data


@app.get("/api/v3/signal/{symbol}")
async def signal(symbol: str, refresh: bool = True, _: None = Depends(require_api_token)) -> dict[str, Any]:
    from v3.publisher import sanitize_for_publish

    sig = await runtime.engine.analyze(symbol.upper(), refresh=refresh)
    sig, violations = sanitize_for_publish(sig, runtime.cfg)
    if violations:
        metrics.record_error("publish.blocked", f"{symbol}: {violations}")
    runtime.store.save_signal(sig)
    return _payload(sig)


@app.get("/api/v3/market")
async def market() -> dict[str, Any]:
    """Market-wide overview (BTC/ETH/global/movers) for "Мой рынок"."""
    return await runtime.data.market_overview()


@app.get("/api/v3/top")
async def top(
    direction: str = Query("", pattern="^(|LONG|SHORT)$"),
    limit: int = Query(20, ge=1, le=50),
    _: None = Depends(require_api_token),
) -> dict[str, Any]:
    """Setups from the last scan, filtered by direction and minimum quality."""
    from v3.scanner import Scanner

    tickers = await runtime.data.tickers()
    scanner = Scanner(runtime.engine, runtime.cfg)
    result = await scanner.run(tickers, limit=runtime.cfg.SCAN_LIMIT, top=runtime.cfg.SCAN_TOP)
    for item in result.analyzed:
        runtime.store.save_signal(item["signal"])
    qmin = runtime.cfg.SCAN_SHOW_QUALITY_MIN
    items = [
        _payload(item["signal"])
        for item in scanner.best_setups(direction or None, quality_min=qmin)
    ][:limit]
    return {
        "direction": direction or "ANY",
        "quality_min": qmin,
        "setups": items,
        "ts_ms": now_ms(),
    }


@app.get("/api/v3/emerging")
async def emerging(
    ignition_min: float = Query(-1.0, ge=-1.0, le=100.0),
    limit: int = Query(20, ge=1, le=50),
    _: None = Depends(require_api_token),
) -> dict[str, Any]:
    """«⚡ Намечается движение»: кандидаты с высоким ignition (ранний отбор)."""
    from v3.scanner import Scanner

    scanner = Scanner(runtime.engine, runtime.cfg)
    if scanner.last is None:
        tickers = await runtime.data.tickers()
        await scanner.run(tickers, limit=runtime.cfg.SCAN_LIMIT, top=runtime.cfg.SCAN_TOP)
    threshold = ignition_min if ignition_min >= 0 else runtime.cfg.EMERGENCE_IGNITION_MIN
    items = []
    for item in scanner.emerging(threshold):
        sig = item["signal"]
        runtime.store.save_signal(sig)
        items.append({
            "symbol": item["candidate"]["symbol"],
            "ignition": item["candidate"].get("ignition", 0.0),
            "early_direction": item["candidate"].get("early_direction", "FLAT"),
            "note": item["candidate"].get("emergence_note", ""),
            "quality": sig.quality,
            "direction": sig.direction,
            "tier": sig.tier,
        })
    return {"ignition_min": threshold, "emerging": items[:limit], "ts_ms": now_ms()}


@app.post("/api/v3/scan")
async def scan(
    limit: int = Query(100, ge=1, le=500),
    top: int = Query(12, ge=1, le=50),
    _: None = Depends(require_api_token),
) -> dict[str, Any]:
    from v3.publisher import sanitize_for_publish
    from v3.scanner import Scanner

    tickers = await runtime.data.tickers()
    scanner = Scanner(runtime.engine, runtime.cfg)
    result = await scanner.run(tickers, limit=limit, top=top)
    for item in result.analyzed:
        sig, violations = sanitize_for_publish(item["signal"], runtime.cfg)
        if violations:
            metrics.record_error("publish.blocked", f"{item['signal'].symbol}: {violations}")
        runtime.store.save_signal(sig)
    now = str(int(time.time() * 1000))
    runtime.store.set_state("last_scan_ms", now)
    runtime.store.set_state("v3_last_scan_ms", now)
    metrics.record_scan()
    best = [_payload(item["signal"]) for item in scanner.best_setups()]
    return {
        **scanner.to_dict(),
        "mode": runtime.mode,
        "ts_ms": now_ms(),
        "tradable": best[:top],
    }


@app.get("/api/v3/backtest/{symbol}")
async def backtest(
    symbol: str,
    tf: str = Query("15m"),
    bars: int = Query(1000, ge=300, le=5000),
    warmup: int = Query(120, ge=50, le=500),
    _: None = Depends(require_api_token),
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
async def history(symbol: str, limit: int = Query(50, ge=1, le=500), _: None = Depends(require_api_token)) -> dict[str, Any]:
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


@app.get("/api/v3/alerts")
async def alerts(_: None = Depends(require_api_token)) -> dict[str, Any]:
    """Состояние и пороги авто-сигналов (то же, что показывает «🔔 АВТО-СИГНАЛЫ»)."""
    cfg = runtime.cfg
    store = runtime.store

    def _state(key: str, default: str = "0") -> str:
        return store.get_state(key, default) or default

    return {
        "enabled": _state("v3_alerts_enabled", "1" if cfg.ALERTS_ENABLED else "0") in ("1", "true", "True"),
        "interval_seconds": cfg.WATCHER_INTERVAL_SECONDS,
        "thresholds": {
            "min_quality": cfg.ALERT_MIN_QUALITY,
            "min_bot_confidence": cfg.ALERT_MIN_BOT_CONFIDENCE,
            "min_data_confidence": cfg.ALERT_MIN_DATA_CONFIDENCE,
            "max_risk_score": cfg.ALERT_MAX_RISK_SCORE,
            "min_rr": cfg.ALERT_MIN_RR,
            "require_fresh": cfg.ALERT_REQUIRE_FRESH,
            "cooldown_seconds": cfg.COOLDOWN_SECONDS,
            "max_per_cycle": cfg.ALERT_MAX_PER_CYCLE,
        },
        "last_cycle_ms": int(_state("v3_last_cycle_ms")),
        "found_total": int(_state("v3_alerts_found")),
        "sent_total": int(_state("v3_alerts_sent")),
        "last_found_symbol": _state("v3_last_found_symbol", ""),
        "last_alert_ms": int(_state("v3_last_alert_ms")),
        "last_alert_symbol": _state("v3_last_alert_symbol", ""),
        "last_suppressed": _state("v3_last_suppressed", ""),
        "active_signals": len(runtime.lifecycle.active()),
        "ts_ms": now_ms(),
    }


@app.post("/api/v3/track")
async def track(prices: dict[str, float], _: None = Depends(require_api_token)) -> dict[str, Any]:
    """Provide a price map {SYMBOL: price}; returns TP/SL lifecycle events."""
    events = runtime.lifecycle.track_prices(prices)
    return {"events": events}


@app.get("/api/v3/calibrate")
async def calibrate(
    symbols: str = Query("BTCUSDT,ETHUSDT"),
    tf: str = Query("15m"),
    bars: int = Query(2000, ge=300, le=10000),
    warmup: int = Query(120, ge=50, le=500),
    _: None = Depends(require_api_token),
) -> dict[str, Any]:
    from v3.calibrate import calibrate

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    res = await calibrate(runtime.engine, syms, tf=tf, bars=bars, warmup=warmup, cfg=runtime.cfg)
    return res.to_dict()


@app.get("/api/v3/glossary/{term}")
async def glossary(term: str) -> dict[str, Any]:
    from v3.tg.render import GLOSSARY

    key = term.lower()
    if key == "list":
        return {"terms": sorted(GLOSSARY.keys() - {"list"})}
    return {"term": key, "explanation": GLOSSARY.get(key, "unknown term")}


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
    _: None = Depends(require_api_token),
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
