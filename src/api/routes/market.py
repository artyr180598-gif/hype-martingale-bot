"""
Market Overview and Ticker Endpoints.
"""
from fastapi import APIRouter

from src.data.adapters.binance import BinanceFuturesAdapter

router = APIRouter(prefix="/api/v1/market", tags=["Market Data"])


@router.get("/overview")
async def get_market_overview():
    adapter = BinanceFuturesAdapter()
    tickers = []
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]:
        try:
            t = await adapter.fetch_ticker(sym)
            tickers.append(t.model_dump())
        except Exception:
            pass

    return {
        "tickers": tickers,
        "breadth": {
            "state": "BULLISH_EXPANSION",
            "pct_above_ema50": 68.0,
            "advance_decline_ratio": 1.8,
        },
    }
