"""
Health and Readiness Probes.
"""
from fastapi import APIRouter

from src.config.settings import settings
from src.core.time_utils import utc_now_ms
from src.database.connection import check_db_health

router = APIRouter(tags=["Health & Monitoring"])


@router.get("/health")
async def health_check():
    db_ok = await check_db_health()
    return {
        "status": "healthy" if db_ok else "degraded",
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp_ms": utc_now_ms(),
        "database": "connected" if db_ok else "disconnected",
    }


@router.get("/ready")
async def readiness_check():
    db_ok = await check_db_health()
    return {"ready": db_ok}


@router.get("/metrics")
async def metrics_endpoint():
    return {
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "system_status": "operational",
        "tracked_symbols_count": len(settings.TRACKED_SYMBOLS),
        "live_trading_enabled": settings.ENABLE_LIVE_TRADING,
    }
