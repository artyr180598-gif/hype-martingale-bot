"""Тесты демо-источника данных."""

import pytest

from src.data.demo import DemoMarketSource
from src.data.models import Instrument, Ticker

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "NOVAUSDT"]


@pytest.mark.asyncio
async def test_instruments(settings):
    src = DemoMarketSource(settings)
    instruments = await src.discover_instruments()
    assert len(instruments) >= 40
    assert all(isinstance(i, Instrument) for i in instruments)
    bybit_style = {i.symbol for i in instruments}
    assert "BTCUSDT" in bybit_style


@pytest.mark.asyncio
async def test_klines_deterministic_and_positive(settings):
    src = DemoMarketSource(settings)
    df1 = await src.get_klines("BTCUSDT", "15m", 300)
    df2 = await src.get_klines("BTCUSDT", "15m", 300)
    assert len(df1) == 300
    assert (df1["close"] > 0).all()
    assert (df1["high"] >= df1["low"]).all()
    assert df1["ts"].is_monotonic_increasing
    pd_test = df1.reset_index(drop=True).equals(df2.reset_index(drop=True))
    assert pd_test, "демо-серии должны быть детерминированными"


@pytest.mark.asyncio
async def test_klines_timeframes(settings):
    src = DemoMarketSource(settings)
    for tf, expect in (("15m", 300), ("1h", 200), ("4h", 100)):
        df = await src.get_klines("ETHUSDT", tf, expect)
        assert len(df) == expect, tf


@pytest.mark.asyncio
async def test_tickers(settings):
    src = DemoMarketSource(settings)
    tickers = await src.get_tickers(["BTCUSDT", "SOLUSDT"])
    assert len(tickers) == 2
    assert all(isinstance(t, Ticker) for t in tickers)
    btc = next(t for t in tickers if t.symbol == "BTCUSDT")
    assert btc.last > 0
    assert btc.turnover_24h > 0
    assert btc.funding_rate is not None


@pytest.mark.asyncio
async def test_fear_greed_and_news(settings):
    src = DemoMarketSource(settings)
    fg = await src.get_fear_greed()
    assert 0 <= fg.value <= 100
    news = await src.get_news(10)
    assert 1 <= len(news) <= 10
    assert all(n.title for n in news)


@pytest.mark.asyncio
async def test_hidden_gem_scenario(settings):
    """В какой-то момент времени на рынке должны быть активные «скрытые» монеты."""
    src = DemoMarketSource(settings)
    tickers = await src.get_tickers()
    movers = [t for t in tickers if abs(t.price_24h_pct) > 8 and t.turnover_24h > 1e6]
    assert len(movers) >= 1, "сценарии должны порождать волатильные монеты"
