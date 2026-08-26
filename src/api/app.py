"""
FastAPI-приложение: JSON API + HTML-дашборд.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from src.api.routes import router
from src.config.settings import settings
from src.core.logging import get_logger

logger = get_logger("api.app")

TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(
    title="HYPE Advisor",
    version=settings.APP_VERSION,
    description="Профессиональный крипто-советник: сканер рынка, поиск скрытых монет, "
    "теханализ, волны, волатильность, планы входа/выхода. Бот не торгует.",
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.include_router(router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request):
    ctx = _ctx()
    ctx.ensure_services()
    watch = []
    if ctx.watcher:
        watch = [r.to_dict() for r in ctx.watcher.get_results()]
    gems = ctx.store.latest_gems(15)
    news = ctx.store.recent_news(12)
    report = ctx.scanner._last_report.to_dict() if ctx.scanner._last_report else None
    positions = ctx.store.positions()
    demo = ctx.mode != "live"
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "mode": ctx.mode.upper(),
            "demo": demo,
            "app_name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "watch": watch,
            "gems": gems,
            "news": news,
            "report": report,
            "positions": positions,
            "watchlist": list(ctx.watcher.watchlist) if ctx.watcher else settings.watchlist,
        },
    )


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    from src.core.context import get_context

    ctx = get_context()
    return JSONResponse(
        {
            "status": "ok",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "mode": ctx.mode,
            "started": ctx.started,
        }
    )


def _ctx():
    from src.core.context import get_context

    return get_context()
