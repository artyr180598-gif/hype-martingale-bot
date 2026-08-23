"""
Async Repositories for Database Access and Persistence.
"""
from typing import Any

from sqlalchemy import and_, delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.database.models import (
    AlertLog,
    BacktestRecord,
    Candle,
    NewsArticle,
    PaperPosition,
    PaperTrade,
    SignalRecord,
    User,
    UserWatchlist,
)

logger = get_logger("database.repositories")


class CandleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 500, end_time_ms: int | None = None
    ) -> list[Candle]:
        stmt = select(Candle).where(
            and_(Candle.symbol == symbol, Candle.timeframe == timeframe)
        )
        if end_time_ms is not None:
            stmt = stmt.where(Candle.timestamp_ms <= end_time_ms)
        stmt = stmt.order_by(desc(Candle.timestamp_ms)).limit(limit)

        result = await self.session.execute(stmt)
        candles = list(result.scalars().all())
        candles.reverse()  # Return chronological order
        return candles

    async def save_candles(self, candles_data: list[dict[str, Any]]) -> int:
        if not candles_data:
            return 0
        count = 0
        for c in candles_data:
            candle = Candle(
                symbol=c["symbol"],
                timeframe=c["timeframe"],
                timestamp_ms=c["timestamp_ms"],
                open=c["open"],
                high=c["high"],
                low=c["low"],
                close=c["close"],
                volume=c["volume"],
                quote_volume=c.get("quote_volume", 0.0),
                trades_count=c.get("trades_count", 0),
                taker_buy_volume=c.get("taker_buy_volume", 0.0),
            )
            self.session.add(candle)
            count += 1
        await self.session.flush()
        return count


class SignalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_signal(self, signal_data: dict[str, Any]) -> SignalRecord:
        record = SignalRecord(**signal_data)
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_top_signals(self, limit: int = 10) -> list[SignalRecord]:
        stmt = (
            select(SignalRecord)
            .where(SignalRecord.direction != "NO_TRADE")
            .order_by(desc(SignalRecord.score), desc(SignalRecord.timestamp_ms))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_symbol_signal(self, symbol: str) -> SignalRecord | None:
        stmt = (
            select(SignalRecord)
            .where(SignalRecord.symbol == symbol)
            .order_by(desc(SignalRecord.timestamp_ms))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def update_signal_outcome(
        self, signal_id: str, outcome: str, realized_r: float, mfe: float, mae: float, closed_at_ms: int
    ) -> None:
        stmt = (
            update(SignalRecord)
            .where(SignalRecord.signal_id == signal_id)
            .values(
                status="CLOSED",
                actual_outcome=outcome,
                realized_r=realized_r,
                mfe_percent=mfe,
                mae_percent=mae,
                closed_at_ms=closed_at_ms,
            )
        )
        await self.session.execute(stmt)


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_user(
        self, telegram_id: int, username: str | None = None, first_name: str | None = None
    ) -> User:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        user = result.scalars().first()
        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
            )
            self.session.add(user)
            await self.session.flush()
        return user

    async def get_watchlist(self, user_id: int) -> list[str]:
        stmt = select(UserWatchlist.symbol).where(UserWatchlist.user_id == user_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add_to_watchlist(self, user_id: int, symbol: str) -> None:
        sym = symbol.upper()
        stmt = select(UserWatchlist).where(
            and_(UserWatchlist.user_id == user_id, UserWatchlist.symbol == sym)
        )
        exists = (await self.session.execute(stmt)).scalars().first()
        if not exists:
            watchlist_item = UserWatchlist(user_id=user_id, symbol=sym)
            self.session.add(watchlist_item)
            await self.session.flush()

    async def remove_from_watchlist(self, user_id: int, symbol: str) -> None:
        stmt = delete(UserWatchlist).where(
            and_(UserWatchlist.user_id == user_id, UserWatchlist.symbol == symbol.upper())
        )
        await self.session.execute(stmt)


class BacktestRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_backtest(self, backtest_data: dict[str, Any]) -> BacktestRecord:
        record = BacktestRecord(**backtest_data)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_recent_backtests(self, limit: int = 10) -> list[BacktestRecord]:
        stmt = select(BacktestRecord).order_by(desc(BacktestRecord.created_at)).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class PaperTradingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_open_positions(self, user_id: int | None = None) -> list[PaperPosition]:
        stmt = select(PaperPosition).where(PaperPosition.status == "OPEN")
        if user_id is not None:
            stmt = stmt.where(PaperPosition.user_id == user_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def save_position(self, pos_data: dict[str, Any]) -> PaperPosition:
        pos = PaperPosition(**pos_data)
        self.session.add(pos)
        await self.session.flush()
        return pos

    async def save_trade(self, trade_data: dict[str, Any]) -> PaperTrade:
        trade = PaperTrade(**trade_data)
        self.session.add(trade)
        await self.session.flush()
        return trade


class AlertRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def has_alert_been_sent(self, event_hash: str) -> bool:
        stmt = select(AlertLog.id).where(AlertLog.event_hash == event_hash).limit(1)
        result = await self.session.execute(stmt)
        return result.scalars().first() is not None

    async def record_alert(self, alert_data: dict[str, Any]) -> AlertLog:
        alert = AlertLog(**alert_data)
        self.session.add(alert)
        await self.session.flush()
        return alert


class NewsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_articles(self, articles: list[dict[str, Any]]) -> int:
        count = 0
        for a in articles:
            # Check existence
            stmt = select(NewsArticle.id).where(NewsArticle.source_id == a["source_id"]).limit(1)
            exists = (await self.session.execute(stmt)).scalars().first()
            if not exists:
                article = NewsArticle(**a)
                self.session.add(article)
                count += 1
        await self.session.flush()
        return count

    async def get_latest_news(self, limit: int = 10) -> list[NewsArticle]:
        stmt = select(NewsArticle).order_by(desc(NewsArticle.published_at_ms)).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
