"""Тесты аналитического движка (на демо-источнике)."""

import pytest

from src.analysis.engine import AnalysisEngine, AnalysisResult
from src.data.demo import DemoMarketSource


@pytest.mark.asyncio
async def test_analyze_btc(settings):
    src = DemoMarketSource(settings)
    engine = AnalysisEngine(src, settings)
    res = await engine.analyze("BTCUSDT")
    assert isinstance(res, AnalysisResult)
    assert res.symbol == "BTCUSDT"
    assert res.price > 0
    assert 0 <= res.score <= 100
    assert res.tier in {"A+", "A", "B", "C", "D"}
    assert res.direction in {"LONG", "SHORT", "WAIT", "NEUTRAL"}
    assert 0 < res.confidence <= 1
    assert res.volatility.state_ru
    assert res.summary


@pytest.mark.asyncio
async def test_analyze_plan_validity(settings):
    src = DemoMarketSource(settings)
    engine = AnalysisEngine(src, settings)
    for sym in ("BTCUSDT", "SOLUSDT", "PEPEUSDT", "NOVAUSDT"):
        res = await engine.analyze(sym)
        d = res.to_dict()
        assert d["plan"] is None or d["plan"]["direction"] == res.direction
        if res.plan:
            p = res.plan
            if p.direction == "LONG":
                assert p.stop_loss < p.entry_zone[0]
                assert all(t > p.entry_zone[1] for t in p.targets)
            else:
                assert p.stop_loss > p.entry_zone[1]
                assert all(t < p.entry_zone[0] for t in p.targets)
            assert p.rr > 0
            assert 1 <= p.leverage <= settings.MAX_LEVERAGE


@pytest.mark.asyncio
async def test_analyze_caching(settings):
    src = DemoMarketSource(settings)
    engine = AnalysisEngine(src, settings)
    r1 = await engine.analyze("ETHUSDT")
    r2 = await engine.analyze("ETHUSDT")
    assert r1 is r2  # кэш


@pytest.mark.asyncio
async def test_analyze_unknown_symbol(settings):
    src = DemoMarketSource(settings)
    engine = AnalysisEngine(src, settings)
    with pytest.raises(Exception):
        await engine.analyze("TOTALLYFAKEUSDT")
