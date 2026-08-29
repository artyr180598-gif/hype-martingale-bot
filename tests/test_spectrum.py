"""Тесты спектрального анализа."""

import pytest

from src.analysis.spectrum import GROUP_WEIGHTS, SpectrumAnalyzer
from src.data.demo import DemoMarketSource


@pytest.fixture()
def analyzer(settings):
    return SpectrumAnalyzer(DemoMarketSource(settings), settings)


@pytest.mark.asyncio
async def test_spectrum_report_structure(analyzer):
    rep = await analyzer.analyze("BTCUSDT")
    assert rep.symbol == "BTCUSDT"
    assert rep.price > 0
    assert len(rep.timeframes) >= 3, "спектр должен покрывать несколько таймфреймов"
    assert rep.direction in {"LONG", "SHORT", "WAIT"}
    assert -1.0 <= rep.total_score <= 1.0
    assert 0.0 <= rep.confluence <= 100.0
    assert 0.0 <= rep.confidence <= 1.0
    assert 0.0 <= rep.tf_alignment <= 1.0


@pytest.mark.asyncio
async def test_spectrum_groups_complete(analyzer):
    rep = await analyzer.analyze("SOLUSDT")
    # все взвешенные группы присутствуют и в пределах [-1, 1]
    for group in GROUP_WEIGHTS:
        assert group in rep.group_scores, group
        assert -1.0 <= rep.group_scores[group] <= 1.0
    assert abs(sum(GROUP_WEIGHTS.values()) - 1.0) < 1e-6, "веса групп должны давать 1.0"


@pytest.mark.asyncio
async def test_spectrum_factors_populated(analyzer):
    rep = await analyzer.analyze("ETHUSDT")
    assert len(rep.factors) >= 12
    for f in rep.factors:
        assert -1.0 <= f.value <= 1.0
        assert f.name and f.detail


@pytest.mark.asyncio
async def test_spectrum_timeframes_are_scored(analyzer):
    rep = await analyzer.analyze("BTCUSDT")
    tfs = {t.timeframe for t in rep.timeframes}
    assert "15m" in tfs and "4h" in tfs
    for t in rep.timeframes:
        assert -1.0 <= t.score <= 1.0
        assert t.trend in {"up", "down", "range"}
        assert 0 <= t.rsi <= 100


@pytest.mark.asyncio
async def test_spectrum_bars_and_summary(analyzer):
    rep = await analyzer.analyze("BTCUSDT")
    bars = rep.bars()
    assert len(bars) == len(rep.group_scores)
    assert all("▰" in b or "▱" in b for b in bars)
    assert rep.summary
    assert rep.symbol.replace("USDT", "") in rep.summary


@pytest.mark.asyncio
async def test_spectrum_serializable(analyzer):
    import json

    rep = await analyzer.analyze("SOLUSDT")
    d = rep.to_dict()
    json.dumps(d)  # не должно падать
    assert d["symbol"] == "SOLUSDT"
    assert isinstance(d["timeframes"], list)
    assert isinstance(d["group_scores"], dict)
    assert "factors" in d


@pytest.mark.asyncio
async def test_spectrum_direction_matches_score(analyzer):
    rep = await analyzer.analyze("BTCUSDT")
    if rep.direction == "LONG":
        assert rep.total_score > 0
    elif rep.direction == "SHORT":
        assert rep.total_score < 0
    else:
        assert abs(rep.total_score) < 0.12


@pytest.mark.asyncio
async def test_spectrum_unknown_symbol_raises(analyzer):
    with pytest.raises(Exception):
        await analyzer.analyze("NOSUCHCOINUSDT")


@pytest.mark.asyncio
async def test_spectrum_news_sentiment_used(analyzer):
    from src.data.models import NewsItem

    news = [
        NewsItem(id="1", ts_ms=0, source="t", title="SOL surge to record high", symbols=["SOL"], sentiment=0.8),
        NewsItem(id="2", ts_ms=0, source="t", title="ETH hack exploit", symbols=["ETH"], sentiment=-0.7),
    ]
    rep = await analyzer.analyze("SOLUSDT", news)
    assert rep.news_count == 1
    assert rep.news_sentiment > 0
    names = [f.name for f in rep.factors]
    assert "Сентимент новостей" in names
