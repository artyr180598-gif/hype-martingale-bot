"""
Pytest configuration and shared test fixtures.
"""

import numpy as np
import pytest

from src.core.time_utils import utc_now_ms
from src.data.models import (
    CandleData,
    OrderBookData,
    TickerData,
)


@pytest.fixture
def sample_candles() -> list[CandleData]:
    """Generate 100 synthetic candlestick bars with a simulated trend and oscillation."""
    candles = []
    base_price = 50000.0
    base_ts = utc_now_ms() - (100 * 15 * 60 * 1000)
    current_p = base_price

    np.random.seed(42)
    for i in range(100):
        ts = base_ts + (i * 15 * 60 * 1000)
        # Random walk with slight upward drift
        step = np.random.normal(15.0, 50.0)
        open_p = current_p
        close_p = open_p + step
        high_p = max(open_p, close_p) + abs(np.random.normal(10.0, 20.0))
        low_p = min(open_p, close_p) - abs(np.random.normal(10.0, 20.0))
        vol = abs(np.random.normal(50.0, 20.0)) + 10.0

        candles.append(
            CandleData(
                symbol="BTCUSDT",
                timeframe="15m",
                timestamp_ms=ts,
                open=round(open_p, 2),
                high=round(high_p, 2),
                low=round(low_p, 2),
                close=round(close_p, 2),
                volume=round(vol, 4),
                quote_volume=round(vol * close_p, 2),
                trades_count=150,
                taker_buy_volume=round(vol * 0.52, 4),
            )
        )
        current_p = close_p

    return candles


@pytest.fixture
def sample_orderbook() -> OrderBookData:
    return OrderBookData(
        symbol="BTCUSDT",
        timestamp_ms=utc_now_ms(),
        bids=[(50000.0 - i * 10.0, 2.5 + i * 0.2) for i in range(20)],
        asks=[(50010.0 + i * 10.0, 2.0 + i * 0.1) for i in range(20)],
    )


@pytest.fixture
def sample_ticker() -> TickerData:
    return TickerData(
        symbol="BTCUSDT",
        timestamp_ms=utc_now_ms(),
        last_price=50000.0,
        mark_price=50005.0,
        index_price=50002.0,
        bid_price=49998.0,
        ask_price=50002.0,
        volume_24h=12000.0,
        quote_volume_24h=600000000.0,
        price_change_24h_percent=2.5,
        high_24h=51000.0,
        low_24h=48500.0,
    )
