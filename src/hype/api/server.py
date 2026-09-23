"""FastAPI server — REST API for ultimate scanner."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

from ..analysis.engine import analyze_bundle
from ..config import Settings, load_config
from ..data.exchanges import ExchangeManager
from ..scanner.universe import build_universe
from ..signals.generator import analysis_to_signal
from ..store.db import SignalStore


class ScanRequest(BaseModel):
    limit: int = 250
    top: int = 20
    min_quality: float = 55.0


class SignalResponse(BaseModel):
    symbol: str
    direction: str
    status: str
    entry: float
    stop_loss: float
    take_profits: list[float]
    risk_reward: float
    quality_score: float
    quality_grade: str
    confidence_pct: float
    expected_move_pct: float
    regime: str
    exchange: str


def create_app(cfg: Settings | None = None) -> FastAPI:
    cfg = cfg or load_config()
    ex_manager = ExchangeManager(cfg)
    store = SignalStore(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(f"API starting {cfg.APP_VERSION} {cfg.APP_RELEASE}")
        yield
        await ex_manager.close()

    app = FastAPI(
        title="HYPE ULTIMATE Scanner",
        version=cfg.APP_VERSION,
        description="Multi-exchange crypto scanner: Binance, Bybit, OKX, MEXC, KuCoin, Gate, Bitget. LONG/SHORT signals with TP/SL, expected move, STOBB/SBM/JUMP, confluence voting.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def check_token(x_api_token: str | None) -> None:
        if cfg.api_token and x_api_token != cfg.api_token:
            raise HTTPException(status_code=401, detail="Invalid API token")

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "version": cfg.APP_VERSION,
            "release": cfg.APP_RELEASE,
            "exchanges": cfg.exchanges_list,
            "primary": cfg.PRIMARY_EXCHANGE,
        }

    @app.get("/api/v1/universe")
    async def universe(limit: int = Query(100, ge=1, le=500), x_api_token: str | None = Header(None)):
        check_token(x_api_token)
        tickers = await build_universe(ex_manager, cfg, max_symbols=limit)
        return [
            {
                "symbol": t.symbol,
                "exchange": t.exchange,
                "last": t.last,
                "volume_24h": t.volume_24h,
                "turnover_24h": t.turnover_24h,
                "change_24h_pct": t.change_24h_pct,
            }
            for t in tickers
        ]

    @app.get("/api/v1/signal/{symbol}")
    async def get_signal(symbol: str, x_api_token: str | None = Header(None)):
        check_token(x_api_token)
        bundle = await ex_manager.get_bundle(symbol.upper(), timeframes=cfg.timeframes)
        if not bundle.has_minimum:
            raise HTTPException(status_code=404, detail=f"No data for {symbol}: {bundle.errors}")
        analysis = analyze_bundle(bundle, cfg)
        sig = analysis_to_signal(analysis, cfg)
        if not sig:
            raise HTTPException(status_code=404, detail="Failed to generate signal")
        store.save_signal(sig)
        return sig.to_dict()

    @app.post("/api/v1/scan")
    async def scan_market(req: ScanRequest, x_api_token: str | None = Header(None)):
        check_token(x_api_token)
        tickers = await build_universe(ex_manager, cfg, max_symbols=req.limit)
        signals = []
        for ticker in tickers[: req.top + 20]:
            try:
                bundle = await ex_manager.get_bundle(ticker.symbol, timeframes=cfg.timeframes)
                if not bundle.has_minimum:
                    continue
                analysis = analyze_bundle(bundle, cfg)
                sig = analysis_to_signal(analysis, cfg)
                if sig and sig.quality_score >= req.min_quality and sig.status == "SIGNAL":
                    signals.append(sig.to_dict())
                    # Save
                    from ..signals.models import Signal as SignalModel

                    # Already saved via to_dict? Save via store
                    # Re-create minimal for store is already done in signal endpoint, but we save here too
                    # Use analysis_to_signal already
                    # store.save_signal(sig) — sig is Signal model
                    store.save_signal(sig)
            except Exception as e:
                logger.debug(f"Scan {ticker.symbol} failed: {e}")
                continue
        # Sort
        signals.sort(key=lambda s: s["quality_score"] * 0.6 + s["confidence_pct"] * 0.4, reverse=True)
        return {"count": len(signals), "signals": signals[: req.top]}

    @app.get("/api/v1/top")
    async def top_signals(
        direction: str | None = Query(None, description="LONG or SHORT"),
        limit: int = Query(20, ge=1, le=100),
        x_api_token: str | None = Header(None),
    ):
        check_token(x_api_token)
        rows = store.get_top(direction=direction, limit=limit)
        return {"count": len(rows), "signals": rows}

    @app.get("/api/v1/history/{symbol}")
    async def history(symbol: str, limit: int = Query(50, ge=1, le=200), x_api_token: str | None = Header(None)):
        check_token(x_api_token)
        rows = store.get_recent(limit=limit, symbol=symbol)
        return {"symbol": symbol.upper(), "count": len(rows), "history": rows}

    @app.get("/api/v1/market")
    async def market_overview(x_api_token: str | None = Header(None)):
        check_token(x_api_token)
        btc = await ex_manager.get_bundle("BTCUSDT", timeframes=["1h"])
        eth = await ex_manager.get_bundle("ETHUSDT", timeframes=["1h"])
        universe = await build_universe(ex_manager, cfg, max_symbols=50)
        gainers = sorted(
            [t for t in universe if t.change_24h_pct is not None],
            key=lambda x: x.change_24h_pct,
            reverse=True,
        )[:10]
        return {
            "btc": {"price": btc.ticker.last if btc.ticker else None, "change_24h": btc.ticker.change_24h_pct if btc.ticker else None},
            "eth": {"price": eth.ticker.last if eth.ticker else None},
            "gainers": [{"symbol": t.symbol, "change": t.change_24h_pct, "price": t.last} for t in gainers],
            "total_universe": len(universe),
        }

    @app.get("/api/v1/alerts")
    async def alerts_status(x_api_token: str | None = Header(None)):
        check_token(x_api_token)
        return {
            "enabled": cfg.ALERTS_ENABLED,
            "interval": cfg.WATCHER_INTERVAL_SECONDS,
            "thresholds": {
                "min_quality": cfg.ALERT_MIN_QUALITY,
                "min_confidence": cfg.ALERT_MIN_BOT_CONFIDENCE,
                "min_data": cfg.ALERT_MIN_DATA_CONFIDENCE,
                "max_risk": cfg.ALERT_MAX_RISK_SCORE,
                "min_rr": cfg.ALERT_MIN_RR,
            },
        }

    return app
