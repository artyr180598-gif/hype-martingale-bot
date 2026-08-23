"""
Backtest Execution Endpoints.
"""
from fastapi import APIRouter, HTTPException, Query

from src.backtesting.engine import BacktestEngine
from src.data.adapters.binance import BinanceFuturesAdapter
from src.data.downloader import HistoricalDataDownloader
from src.strategies.registry import StrategyRegistry

router = APIRouter(prefix="/api/v1/backtest", tags=["Backtesting & Research"])


@router.get("/run")
async def run_backtest_api(
    symbol: str = Query(default="BTCUSDT"),
    timeframe: str = Query(default="15m"),
    strategy_name: str = Query(default="TrendFollowingStrategy"),
    lookback_bars: int = Query(default=500),
):
    strat = StrategyRegistry.get(strategy_name)
    if not strat:
        available = [s.name for s in StrategyRegistry.list_all()]
        raise HTTPException(status_code=400, detail=f"Strategy '{strategy_name}' not found. Available: {available}")

    downloader = HistoricalDataDownloader(BinanceFuturesAdapter())
    candles = await downloader.get_or_download_candles(symbol, timeframe, lookback_bars=lookback_bars)

    if len(candles) < 50:
        raise HTTPException(status_code=400, detail="Insufficient candle data downloaded for backtest")

    engine = BacktestEngine(strategy=strat)
    result = engine.run(candles)

    return {
        "backtest_id": result.backtest_id,
        "strategy": result.strategy_name,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "metrics": result.metrics.__dict__,
        "trades_count": len(result.trades),
        "recent_trades": result.trades[-10:],
    }
