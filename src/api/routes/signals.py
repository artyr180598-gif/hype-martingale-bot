"""
Signals and Analysis Endpoints.
"""
from fastapi import APIRouter, HTTPException

from src.scanner.market_scanner import MarketScanner

router = APIRouter(prefix="/api/v1/signals", tags=["Signals & Intelligence"])
scanner = MarketScanner()


@router.get("/top")
async def get_top_signals():
    setups = await scanner.scan_market()
    return {"count": len(setups), "setups": [s.model_dump() for s in setups]}


@router.get("/analyze/{symbol}")
async def analyze_symbol(symbol: str):
    sym = symbol.upper()
    if not sym.endswith("USDT") and not sym.endswith("USDC"):
        sym += "USDT"

    setup = await scanner.scan_symbol(sym)
    if not setup:
        raise HTTPException(status_code=404, detail=f"Market data unavailable for {symbol}")
    return setup.model_dump()
