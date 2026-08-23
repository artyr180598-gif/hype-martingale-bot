"""
Paper Trading Virtual Portfolio Endpoints.
"""
from fastapi import APIRouter

from src.bot.handlers import paper_engine

router = APIRouter(prefix="/api/v1/paper", tags=["Paper Trading"])


@router.get("/portfolio")
async def get_portfolio_status():
    p = paper_engine.portfolio
    return {
        "initial_balance": p.initial_balance,
        "cash_balance": p.cash_balance,
        "total_equity": p.total_equity,
        "margin_used": p.margin_used,
        "available_balance": p.available_balance,
        "unrealized_pnl": p.total_unrealized_pnl,
        "total_return_pct": p.total_return_pct,
        "win_rate_pct": p.win_rate_pct,
        "open_positions": [pos.model_dump() for pos in p.open_positions.values()],
        "closed_trades_count": p.closed_trades_count,
        "recent_trades": paper_engine.closed_trades[-10:],
    }
