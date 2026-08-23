"""
FastAPI Application Factory.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.middleware import ObservabilityMiddleware
from src.api.routes import ai, backtest, bot_sim, dashboard, health, market, paper, signals
from src.config.settings import settings
from src.database.connection import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    yield
    # Shutdown


def create_app() -> FastAPI:
    app = FastAPI(
        title="Crypto Futures Quantitative Intelligence Platform API",
        description="Production-grade API for crypto perpetual futures analytics, strategy signals, backtesting, and risk management.",
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Observability
    app.add_middleware(ObservabilityMiddleware)

    # Register Routers
    app.include_router(dashboard.router)
    app.include_router(health.router)
    app.include_router(market.router)
    app.include_router(signals.router)
    app.include_router(backtest.router)
    app.include_router(paper.router)
    app.include_router(ai.router)
    app.include_router(bot_sim.router)

    return app


app = create_app()
