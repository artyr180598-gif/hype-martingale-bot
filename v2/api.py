"""
HTTP-API v2 (FastAPI).

Минимальный набор эндпоинтов, чтобы отчёты можно было смотреть не только в
консоли/Telegram, но и в браузере или дашборде:

  GET  /health          — живость, метрики, ошибки;
  GET  /scan            — запустить трёхуровневый скан (Markdown или JSON);
  GET  /analyze/{query} — отчёт по адресу/символу (?deposit=1000&format=json);
  GET  /status          — состояние ассистента.

API необязательно: без установленного fastapi команда ``serve`` честно
сообщит об этом, остальные режимы продолжат работать.
"""

from __future__ import annotations

from typing import Any

from v2.bot import AssistantCore
from v2.config import V2Config
from v2.core.logging import get_logger
from v2.core.monitor import health, monitor
from v2.reporter import render_report, render_scan

logger = get_logger("api")


def create_app(config: V2Config, core: AssistantCore):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, PlainTextResponse

    app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION)

    @app.get("/health")
    async def get_health() -> dict[str, Any]:
        return {
            "ok": True,
            "app": config.APP_NAME,
            "version": config.APP_VERSION,
            "mode": config.DATA_MODE,
            "health": health.snapshot(),
            "errors": monitor.snapshot(),
        }

    @app.get("/scan")
    async def get_scan(
        limit: int = Query(150, ge=1, le=500),
        analyze_top: int = Query(3, ge=0, le=20),
        format: str = Query("md"),
    ):
        result = await core.pipeline.run(limit=limit, analyze_top=analyze_top)
        if format == "json":
            return result.to_dict()
        return PlainTextResponse(render_scan(result, config), media_type="text/markdown; charset=utf-8")

    @app.get("/analyze/{query}")
    async def get_analyze(
        query: str,
        deposit: float | None = Query(None, gt=0),
        format: str = Query("md"),
    ):
        try:
            report = await core.engine.analyze(query, deposit_usd=deposit)
        except Exception as exc:  # noqa: BLE001 — HTTP должен вернуть 4xx, а не 500-стек
            logger.warning("analyze %s: %s", query, exc)
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if format == "json":
            return report.to_dict()
        return PlainTextResponse(render_report(report, config), media_type="text/markdown; charset=utf-8")

    @app.get("/status")
    async def get_status() -> dict[str, Any]:
        return {
            "filters": config.filters_summary(),
            "provider": getattr(core.provider, "stats", lambda: {})(),
            "ai": core.ai.stats(),
            "executor": core.executor.stats(),
            "messages_handled": core.messages_handled,
            "analyses": core.engine.analyses,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (
            "<html><head><meta charset='utf-8'><title>HYPE Advisor v2</title></head>"
            "<body style='font-family:system-ui;max-width:820px;margin:40px auto;padding:0 16px'>"
            f"<h1>🔥 {config.APP_NAME}</h1>"
            f"<p>Режим данных: <code>{config.DATA_MODE}</code></p>"
            "<ul>"
            "<li><a href='/scan'>/scan</a> — трёхуровневый скан рынка</li>"
            "<li><a href='/analyze/AURORA'>/analyze/AURORA</a> — отчёт по монете "
            "(или <code>/analyze/0x…?format=json</code>)</li>"
            "<li><a href='/status'>/status</a> — фильтры и метрики</li>"
            "<li><a href='/health'>/health</a> — живость и ошибки</li>"
            "</ul>"
            "<p>⚠️ Аналитика, а не финансовая рекомендация.</p>"
            "</body></html>"
        )

    return app
