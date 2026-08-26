"""Тесты настроек (совместимость с переменными старого бота на Railway)."""

import pytest

from src.config.settings import Settings


def test_old_telegram_env_names(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "777")
    s = Settings(_env_file=None)
    assert s.TELEGRAM_BOT_TOKEN == "123:tok"
    assert s.TELEGRAM_ADMIN_CHAT_ID == "777"
    assert s.telegram_enabled


def test_new_telegram_env_names(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "456:tok2")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "888")
    s = Settings(_env_file=None)
    assert s.TELEGRAM_BOT_TOKEN == "456:tok2"
    assert s.TELEGRAM_ADMIN_CHAT_ID == "888"


def test_watchlist_parsing(tmp_path):
    s = Settings(
        _env_file=None, DATA_DIR=tmp_path, WATCHLIST_SYMBOLS=" BTCUSDT, ethusdt ,SOLUSDT, BTCUSDT "
    )
    assert s.watchlist == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_paths_created(tmp_path):
    s = Settings(_env_file=None, DATA_DIR=tmp_path)
    p = s.db_path
    assert p.parent.exists()
    c = s.chart_dir
    assert c.exists()
