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


def test_empty_leverage_env_is_auto(tmp_path):
    """DEFAULT_LEVERAGE= (пусто) не должен ронять запуск — это режим «авто»."""
    from src.config.settings import Settings

    env = tmp_path / "t.env"
    env.write_text("DEFAULT_LEVERAGE=\nDEFAULT_DEPOSIT_USD=250\n", encoding="utf-8")
    s = Settings(_env_file=str(env))
    assert s.DEFAULT_LEVERAGE is None
    assert s.DEFAULT_DEPOSIT_USD == 250.0


def test_explicit_leverage_env(tmp_path):
    from src.config.settings import Settings

    env = tmp_path / "t.env"
    env.write_text("DEFAULT_LEVERAGE=5\n", encoding="utf-8")
    assert Settings(_env_file=str(env)).DEFAULT_LEVERAGE == 5


def test_env_example_is_parseable():
    """Файл .env.example из репозитория должен валидно читаться настройками."""
    from pathlib import Path

    from src.config.settings import Settings

    example = Path(__file__).resolve().parent.parent / ".env.example"
    assert example.exists()
    s = Settings(_env_file=str(example))
    assert s.MARKET_DATA_MODE == "auto"
    assert s.DEFAULT_LEVERAGE is None
    assert s.DEFAULT_EXCHANGE in ("bybit", "binance")
    assert len(s.watchlist) >= 5


def test_telegram_and_bybit_aliases(tmp_path):
    """Railway-имена переменных (TELEGRAM_TOKEN, BYBIT_KEY) должны подхватываться."""
    from src.config.settings import Settings

    env = tmp_path / "t.env"
    env.write_text(
        "TELEGRAM_TOKEN=abc\nTELEGRAM_CHAT_ID=123\nBYBIT_KEY=k\nBYBIT_SECRET=s\n",
        encoding="utf-8",
    )
    s = Settings(_env_file=str(env))
    assert s.TELEGRAM_BOT_TOKEN == "abc"
    assert s.TELEGRAM_ADMIN_CHAT_ID == "123"
    assert s.BYBIT_API_KEY == "k"
    assert s.BYBIT_API_SECRET == "s"
    assert s.telegram_enabled is True
