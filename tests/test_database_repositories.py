"""
Tests for Async Database Repositories and Persistence.
"""
import pytest

from src.core.time_utils import utc_now_ms
from src.database.connection import get_db_session, init_db
from src.database.repositories import (
    AlertRepository,
    UserRepository,
)


@pytest.mark.asyncio
async def test_database_repositories():
    await init_db()

    async for session in get_db_session():
        user_repo = UserRepository(session)
        user = await user_repo.get_or_create_user(telegram_id=987654321, username="trader_pro")
        assert user.telegram_id == 987654321

        # Watchlist
        await user_repo.add_to_watchlist(user.id, "ETHUSDT")
        wl = await user_repo.get_watchlist(user.id)
        assert "ETHUSDT" in wl

        # Alerts
        alert_repo = AlertRepository(session)
        event_hash = f"hash_test_{utc_now_ms()}"
        assert await alert_repo.has_alert_been_sent(event_hash) is False

        await alert_repo.record_alert({
            "user_id": user.id,
            "event_hash": event_hash,
            "symbol": "BTCUSDT",
            "alert_type": "HIGH_CONFIDENCE_SETUP",
            "score": 88.0,
            "message": "BTC LONG setup",
            "sent_successfully": True,
        })
        assert await alert_repo.has_alert_been_sent(event_hash) is True
