"""Конфигурация: пороги, переключатели фильтров, чтение из окружения."""

from __future__ import annotations

from v2.config import V2Config, load_config


def test_defaults_match_spec():
    """Пороги уровня 1 из ТЗ: $500k за 5 минут и 100 транзакций."""
    cfg = V2Config(_env_file=None)
    assert cfg.L1_MIN_VOLUME_5M_USD == 500_000.0
    assert cfg.L1_MIN_TX_5M == 100
    assert cfg.L2_MAX_TOP10_PCT == 40.0
    assert cfg.L2_MIN_LP_LOCK_DAYS == 180
    assert cfg.MIN_RISK_REWARD == 2.0


def test_every_filter_has_a_toggle():
    cfg = V2Config(_env_file=None)
    summary = cfg.filters_summary()
    for key in ("L1 quick", "L2 scam", "L3 onchain", "holders", "lp_lock", "contract"):
        assert key in summary


def test_env_override(monkeypatch):
    monkeypatch.setenv("L1_MIN_VOLUME_5M_USD", "1234567")
    monkeypatch.setenv("SCAN_L2_ENABLED", "false")
    monkeypatch.setenv("ATR_SL_MULTIPLIER", "2.5")
    cfg = V2Config(_env_file=None)
    assert cfg.L1_MIN_VOLUME_5M_USD == 1_234_567.0
    assert cfg.SCAN_L2_ENABLED is False
    assert cfg.ATR_SL_MULTIPLIER == 2.5


def test_ai_disabled_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = V2Config(_env_file=None)
    assert cfg.ai_available is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert V2Config(_env_file=None).ai_available is True


def test_quote_whitelist_and_blocklist_parsed():
    cfg = V2Config(_env_file=None)
    assert "USDC" in cfg.quote_whitelist
    assert "USDT" in cfg.blocklist_symbols
    assert "AURORA" not in cfg.blocklist_symbols


def test_load_config_overrides():
    cfg = load_config(DATA_MODE="demo", DEFAULT_DEPOSIT_USD=250.0)
    assert cfg.DATA_MODE == "demo"
    assert cfg.DEFAULT_DEPOSIT_USD == 250.0


def test_invalid_risk_value_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        V2Config(_env_file=None, RISK_PER_TRADE_PCT=-1.0)
