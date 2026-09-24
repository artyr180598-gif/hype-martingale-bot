import time
from collections import deque

from src.strategies.pump_scanner import PumpScanner, PumpSettings, PumpSignal


def test_default_settings_keep_first_two_filters_enabled():
    s = PumpSettings()
    assert s.interval_seconds > 0
    assert s.threshold_pct > 0


def test_settings_round_trip(tmp_path, monkeypatch):
    import src.strategies.pump_scanner as module

    path = tmp_path / "settings.json"
    monkeypatch.setattr(module, "SETTINGS_PATH", path)
    original = PumpSettings(interval_seconds=60, threshold_pct=3.0, signal_types="PUMP")
    original.save()
    loaded = PumpSettings.load()
    assert loaded.interval_seconds == 60
    assert loaded.threshold_pct == 3.0
    assert loaded.signal_types == "PUMP"


def test_score_is_bounded():
    scanner = PumpScanner()
    assert 0 <= scanner._score("PUMP", 100, 50, 100, 100, {"5": 90}, 50) <= 10
    assert 0 <= scanner._score("DUMP", -100, -50, 0, 0, {}, None) <= 10


def test_beginner_signal_is_explicit():
    scanner = PumpScanner()
    signal = PumpSignal(
        symbol="TESTUSDT",
        direction="PUMP",
        change_pct=5.0,
        start_price=1.0,
        current_price=1.05,
        day_pct=12.0,
        imbalance_buy_pct=55.0,
        volume_24h=1_000_000,
        volume_spike=3.0,
        funding_rate=0.0001,
        open_interest=1000,
        open_interest_change_pct=4.0,
        listing_ms=int((time.time() - 86400) * 1000),
        rsi={"5": 82.0},
        score=8,
        trade_action="WAIT",
        trade_reason="Нет подтверждения",
        entry_low=None,
        entry_high=None,
        stop_price=None,
        tp1=None,
        tp2=None,
        ts=time.time(),
    )
    text = scanner.format_signal(signal)
    assert "Сценарий: ЖДАТЬ" in text
    assert "входить по одному Pump" in text


async def _fake_enrich(ticker, direction, change, start, day_pct):
    return PumpSignal(
        symbol=ticker["symbol"],
        direction=direction,
        change_pct=change,
        start_price=start,
        current_price=float(ticker["lastPrice"]),
        day_pct=day_pct,
        imbalance_buy_pct=None,
        volume_24h=0,
        volume_spike=None,
        funding_rate=None,
        open_interest=None,
        open_interest_change_pct=None,
        listing_ms=None,
        rsi={},
        score=5,
        trade_action="WAIT",
        trade_reason="test",
        entry_low=None,
        entry_high=None,
        stop_price=None,
        tp1=None,
        tp2=None,
        ts=time.time(),
    )


def test_update_detects_pump_from_rolling_window(monkeypatch):
    scanner = PumpScanner()
    scanner.running = True
    scanner.history_ready = True
    scanner.settings = PumpSettings(
        interval_seconds=300,
        threshold_pct=5.0,
        cooldown_seconds=300,
        signal_types="BOTH",
    )
    now = time.time()
    scanner.ws_symbols = ["TESTUSDT"]
    scanner.ticker_cache = {
        "TESTUSDT": {
            "symbol": "TESTUSDT",
            "lastPrice": "105",
            "price24hPcnt": "0.02",
        }
    }
    scanner.prices["TESTUSDT"] = deque(
        [(now - 60, 100.0), (now - 30, 101.0)]
    )
    monkeypatch.setattr(scanner, "_enrich", _fake_enrich)
    signals = __import__("asyncio").run(scanner.update())
    assert len(signals) == 1
    assert signals[0].direction == "PUMP"
    assert signals[0].change_pct >= 5.0


def test_update_detects_dump_from_rolling_window(monkeypatch):
    scanner = PumpScanner()
    scanner.running = True
    scanner.history_ready = True
    scanner.settings = PumpSettings(
        interval_seconds=300,
        threshold_pct=5.0,
        cooldown_seconds=300,
        signal_types="DUMP",
    )
    now = time.time()
    scanner.ws_symbols = ["TESTUSDT"]
    scanner.ticker_cache = {
        "TESTUSDT": {
            "symbol": "TESTUSDT",
            "lastPrice": "95",
            "price24hPcnt": "-0.02",
        }
    }
    scanner.prices["TESTUSDT"] = deque(
        [(now - 60, 100.0), (now - 30, 99.0)]
    )
    monkeypatch.setattr(scanner, "_enrich", _fake_enrich)
    signals = __import__("asyncio").run(scanner.update())
    assert len(signals) == 1
    assert signals[0].direction == "DUMP"
    assert signals[0].change_pct <= -5.0


def test_update_respects_cooldown(monkeypatch):
    scanner = PumpScanner()
    scanner.running = True
    scanner.history_ready = True
    scanner.settings = PumpSettings(
        interval_seconds=300,
        threshold_pct=5.0,
        cooldown_seconds=300,
    )
    now = time.time()
    scanner.ws_symbols = ["TESTUSDT"]
    scanner.ticker_cache = {
        "TESTUSDT": {"symbol": "TESTUSDT", "lastPrice": "105"}
    }
    scanner.prices["TESTUSDT"] = deque([(now - 30, 100.0)])
    monkeypatch.setattr(scanner, "_enrich", _fake_enrich)
    first = __import__("asyncio").run(scanner.update())
    second = __import__("asyncio").run(scanner.update())
    assert len(first) == 1
    assert second == []
