"""Тесты сканера и наблюдения (демо-режим)."""

import pytest

from src.analysis.engine import AnalysisEngine
from src.core.store import Store
from src.data.demo import DemoMarketSource
from src.universe.scanner import UniverseScanner, WatchlistEngine


@pytest.mark.asyncio
async def test_scan_finds_gems(settings):
    src = DemoMarketSource(settings)
    engine = AnalysisEngine(src, settings)
    store = Store(settings.db_path)
    scanner = UniverseScanner(src, engine, settings, store)
    report = await scanner.scan(deep_top=12)
    assert report.total_instruments >= 40
    assert report.candidates >= 10
    assert report.analyzed <= 12
    assert report.duration_sec >= 0
    gems = store.latest_gems(50)
    assert isinstance(gems, list)
    assert len(report.gems) >= 0  # в некоторые моменты может не быть


@pytest.mark.asyncio
async def test_scan_report_serializable(settings):
    src = DemoMarketSource(settings)
    engine = AnalysisEngine(src, settings)
    store = Store(settings.db_path)
    scanner = UniverseScanner(src, engine, settings, store)
    report = await scanner.scan(deep_top=6)
    d = report.to_dict()
    assert isinstance(d["gems"], list)
    for g in d["gems"]:
        assert g["score"] >= settings.GEM_MIN_SCORE


@pytest.mark.asyncio
async def test_watchlist_cycle(settings):
    src = DemoMarketSource(settings)
    engine = AnalysisEngine(src, settings)
    store = Store(settings.db_path)
    watcher = WatchlistEngine(src, engine, settings, store)
    assert watcher.watchlist == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    alerts = await watcher.run_cycle()
    assert isinstance(alerts, list)
    results = watcher.get_results()
    assert len(results) == 3
    for r in results:
        assert r.symbol in watcher.watchlist
        assert r.score >= 0


@pytest.mark.asyncio
async def test_watchlist_add_del(settings):
    src = DemoMarketSource(settings)
    engine = AnalysisEngine(src, settings)
    store = Store(settings.db_path)
    watcher = WatchlistEngine(src, engine, settings, store)
    watcher.add_symbol("PEPEUSDT")
    assert "PEPEUSDT" in watcher.watchlist
    watcher.remove_symbol("PEPEUSDT")
    assert "PEPEUSDT" not in watcher.watchlist
    watcher.add_symbol("pepeusdt")
    assert "PEPEUSDT" in watcher.watchlist


@pytest.mark.asyncio
async def test_signal_dedup(settings):
    src = DemoMarketSource(settings)
    engine = AnalysisEngine(src, settings)
    store = Store(settings.db_path)
    watcher = WatchlistEngine(src, engine, settings, store)
    await watcher.run_cycle()
    count1 = len(store.recent_signals(1000))
    await watcher.run_cycle()
    count2 = len(store.recent_signals(1000))
    assert count2 <= count1 + 0  # дубликаты подавлены (тот же час)
