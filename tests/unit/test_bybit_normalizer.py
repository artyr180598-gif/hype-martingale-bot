from decimal import Decimal

from app.data.exchanges.bybit import BybitLinearAdapter
from app.data.models import MarketEventType, TickerEvent, TradeEvent


def test_normalizes_ticker() -> None:
    payload = {
        "topic": "tickers.BTCUSDT",
        "ts": 1710000000000,
        "data": {
            "symbol": "BTCUSDT",
            "lastPrice": "70000",
            "markPrice": "70001",
            "indexPrice": "70002",
            "bid1Price": "69999",
            "bid1Size": "1.2",
            "ask1Price": "70000",
            "ask1Size": "0.8",
            "openInterest": "1000",
            "fundingRate": "0.0001",
        },
    }
    events = BybitLinearAdapter()._normalize(payload)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, TickerEvent)
    assert event.event_type is MarketEventType.TICKER
    assert event.last_price == Decimal("70000")
    assert event.open_interest == Decimal("1000")


def test_normalizes_all_trades_in_batch() -> None:
    payload = {
        "topic": "publicTrade.BTCUSDT",
        "ts": 1710000000000,
        "data": [
            {"s": "BTCUSDT", "T": 1710000000001, "p": "70000", "v": "0.1", "S": "Buy", "i": "1"},
            {"s": "BTCUSDT", "T": 1710000000002, "p": "70001", "v": "0.2", "S": "Sell", "i": "2"},
        ],
    }
    events = BybitLinearAdapter()._normalize(payload)
    assert len(events) == 2
    assert all(isinstance(event, TradeEvent) for event in events)
    assert events[1].side == "sell"
