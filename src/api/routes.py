"""REST API советника."""

from __future__ import annotations

import io

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from src.core.context import get_context
from src.core.logging import get_logger
from src.core.timeutil import now_ms

logger = get_logger("api.routes")
router = APIRouter(prefix="/api")


@router.get("/status")
async def status() -> dict:
    ctx = get_context()
    ctx.ensure_services()
    return {
        "mode": ctx.mode,
        "demo": ctx.mode != "live",
        "watchlist": list(ctx.watcher.watchlist) if ctx.watcher else [],
        "watched": len(ctx.watcher.results) if ctx.watcher else 0,
        "last_watch_cycle": ctx.watcher.last_cycle_ms if ctx.watcher else 0,
        "last_scan": ctx.scanner._last_report.to_dict() if ctx.scanner._last_report else None,
        "signals_count": len(ctx.store.recent_signals(1000)),
        "positions_open": len(ctx.store.positions("open")),
    }


@router.get("/watch")
async def watch_list() -> dict:
    ctx = get_context()
    ctx.ensure_services()
    results = ctx.watcher.get_results() if ctx.watcher else []
    return {"count": len(results), "results": [r.to_dict() for r in results]}


@router.get("/watch/{symbol}")
async def watch_one(symbol: str, refresh: bool = False) -> dict:
    ctx = get_context()
    ctx.ensure_services()
    symbol = symbol.upper()
    try:
        res = await ctx.engine.analyze(symbol, refresh=refresh)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(e)) from e
    return res.to_dict()


@router.post("/watch")
async def watch_add(payload: dict) -> dict:
    ctx = get_context()
    ctx.ensure_services()
    symbol = str(payload.get("symbol", "")).strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol обязателен")
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    ctx.watcher.add_symbol(symbol)
    return {"ok": True, "watchlist": list(ctx.watcher.watchlist)}


@router.delete("/watch/{symbol}")
async def watch_del(symbol: str) -> dict:
    ctx = get_context()
    ctx.ensure_services()
    ctx.watcher.remove_symbol(symbol.upper())
    return {"ok": True, "watchlist": list(ctx.watcher.watchlist)}


@router.post("/scan")
async def trigger_scan(deep_top: int | None = None) -> dict:
    ctx = get_context()
    ctx.ensure_services()
    report = await ctx.scanner.scan(deep_top=deep_top)
    return report.to_dict()


@router.get("/scan")
async def last_scan() -> dict:
    ctx = get_context()
    ctx.ensure_services()
    if ctx.scanner._last_report is None:
        raise HTTPException(status_code=404, detail="Скан ещё не запускался")
    return ctx.scanner._last_report.to_dict()


@router.get("/gems")
async def gems(limit: int = Query(default=15, le=100)) -> dict:
    ctx = get_context()
    return {"gems": ctx.store.latest_gems(limit)}


@router.get("/signal/{symbol}")
async def signal(symbol: str) -> dict:
    ctx = get_context()
    ctx.ensure_services()
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    try:
        res = await ctx.engine.analyze(symbol, refresh=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(e)) from e
    return res.to_dict()


@router.get("/chart/{symbol}")
async def chart(symbol: str) -> Response:
    ctx = get_context()
    ctx.ensure_services()
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    try:
        from src.charts.renderer import chart_to_buffer
        from src.data.indicators import compute_all

        res = await ctx.engine.analyze(symbol)
        df = await ctx.source.get_klines(symbol, "15m", 300)
        buf = chart_to_buffer(compute_all(df), res)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(e)) from e
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/positions")
async def positions() -> dict:
    ctx = get_context()
    return {"positions": ctx.store.positions()}


@router.get("/news")
async def news(limit: int = Query(default=20, le=100)) -> dict:
    ctx = get_context()
    return {"news": ctx.store.recent_news(limit)}


@router.get("/fear")
async def fear() -> dict:
    ctx = get_context()
    ctx.ensure_services()
    try:
        fg = await ctx.source.get_fear_greed()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"value": fg.value, "classification": fg.classification, "ts_ms": fg.ts_ms}


@router.get("/global")
async def global_stats() -> dict:
    ctx = get_context()
    ctx.ensure_services()
    try:
        return await ctx.source.get_global_stats()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e
