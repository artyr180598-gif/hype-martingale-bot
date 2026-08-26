"""Тесты хранилища."""

from src.core.store import Store
from src.core.timeutil import now_ms


def test_signals_roundtrip(settings):
    store = Store(settings.db_path)
    store.save_signal(now_ms(), "SOLUSDT", "LONG", 81.5, "A+", {"plan": {"rr": 2.5}})
    sigs = store.recent_signals(10)
    assert len(sigs) == 1
    assert sigs[0]["symbol"] == "SOLUSDT"
    assert sigs[0]["score"] == 81.5
    assert sigs[0]["payload"]["plan"]["rr"] == 2.5
    by_symbol = store.recent_signals(10, symbol="SOLUSDT")
    assert len(by_symbol) == 1


def test_positions_lifecycle(settings):
    store = Store(settings.db_path)
    store.upsert_position("SOLUSDT", "LONG", 100.0, 95.0, [105.0, 110.0])
    positions = store.positions("open")
    assert len(positions) == 1
    assert positions[0]["targets"] == [105.0, 110.0]
    # upsert перезаписывает
    store.upsert_position("SOLUSDT", "LONG", 101.0, 96.0, [106.0])
    positions = store.positions("open")
    assert positions[0]["entry"] == 101.0
    # закрытие с PnL
    store.close_position("SOLUSDT", 108.0, "цель 1")
    positions = store.positions()
    assert positions[0]["status"] == "closed"
    assert positions[0]["pnl_pct"] > 0


def test_gems_and_news(settings):
    store = Store(settings.db_path)
    ts = now_ms()
    store.save_gems(ts, [{"symbol": "PEPEUSDT", "source": "scanner", "score": 88.0, "reason": "памп"}])
    gems = store.latest_gems(10)
    assert len(gems) == 1
    assert gems[0]["score"] == 88.0
    store.save_news(
        [{"id": "n1", "ts_ms": ts, "source": "test", "title": "Пампим", "symbols": ["PEPEUSDT"], "sentiment": 0.9}]
    )
    news = store.recent_news(5)
    assert len(news) == 1
    assert news[0]["sentiment"] == 0.9


def test_state(settings):
    store = Store(settings.db_path)
    store.set_state("mode", "demo")
    assert store.get_state("mode") == "demo"
    assert store.get_state("missing", "def") == "def"
