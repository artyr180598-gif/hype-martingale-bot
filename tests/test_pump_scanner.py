import time

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
