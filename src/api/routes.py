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


@router.get("/backtest/{symbol}")
async def backtest(
    symbol: str,
    days: float = Query(30.0, ge=1, le=365),
    tf: str = Query("1h", pattern="^(5m|15m|30m|1h|4h)$"),
    bars: int | None = Query(None, ge=300, le=20000),
    step: int = Query(1, ge=1, le=48),
    min_rr: float = Query(1.5, ge=0.1, le=10),
    allow_short: bool = Query(True),
) -> dict:
    """
    Прогон советника по истории: те же `analyze_frames`, что и в живом режиме.
    Метрики — в R (кратностях риска), поэтому сравнимы между монетами.
    """
    from src.backtest.engine import BacktestConfig
    from src.backtest.report import backtest_report
    from src.backtest.service import run_backtest

    ctx = get_context()
    await ctx.ensure_ready()
    medium = {"5m": "15m", "15m": "1h", "30m": "2h", "1h": "4h", "4h": "1d"}.get(tf, "4h")
    cfg = BacktestConfig(entry_tf=tf, medium_tf=medium, macro_tf="1d",
                         warmup_bars=200, step=step, min_rr=min_rr, allow_short=allow_short)
    try:
        res = await run_backtest(ctx.source, ctx.engine, symbol.upper(), cfg,
                                 period_days=days, bars=bars)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(e)) from e
    d = res.to_dict()
    d["report_text"] = backtest_report(res)
    return d


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


@router.get("/spectrum/{symbol}")
async def spectrum(symbol: str) -> dict:
    """Полный спектральный анализ: 5 таймфреймов × 8 групп факторов."""
    from src.analysis.spectrum import SpectrumAnalyzer

    ctx = get_context()
    ctx.ensure_services()
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    try:
        news = await ctx.source.get_news(20)
        report = await SpectrumAnalyzer(ctx.source, ctx.settings).analyze(symbol, news)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(e)) from e
    return report.to_dict()


@router.get("/trade-card/{symbol}")
async def trade_card(
    symbol: str,
    deposit: float | None = None,
    risk_pct: float | None = None,
    leverage: int | None = None,
    exchange: str = "bybit",
    market: str = "futures",
) -> dict:
    """Карточка сделки: объём позиции, плечо, пошаговая инструкция."""
    from src.analysis.advisor import TradeAdvisor

    ctx = get_context()
    ctx.ensure_services()
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    try:
        res = await ctx.engine.analyze(symbol, refresh=True)
        card = await TradeAdvisor(ctx.source, ctx.settings).build(
            res,
            deposit_usd=deposit,
            risk_pct=risk_pct,
            leverage=leverage,
            exchange=exchange,
            market=market,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(e)) from e
    return card.to_dict()


@router.get("/spectrum-chart/{symbol}")
async def spectrum_chart(symbol: str) -> Response:
    from src.analysis.spectrum import SpectrumAnalyzer
    from src.charts.spectrum import chart_spectrum

    ctx = get_context()
    ctx.ensure_services()
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    try:
        report = await SpectrumAnalyzer(ctx.source, ctx.settings).analyze(symbol)
        path = ctx.settings.chart_dir / f"{symbol}_spectrum.png"
        chart_spectrum(report, path)
        content = path.read_bytes()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(e)) from e
    return Response(content=content, media_type="image/png")


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
