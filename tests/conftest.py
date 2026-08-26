"""Фикстуры тестов: демо-источник и настройки."""

from __future__ import annotations

import asyncio

import pytest

from src.config.settings import Settings
from src.data.demo import DemoMarketSource
from src.data.indicators import compute_all


def make_settings(tmp_path) -> Settings:
    s = Settings(
        _env_file=None,
        MARKET_DATA_MODE="demo",
        DATA_DIR=tmp_path,
        DB_PATH=tmp_path / "test.db",
        CHART_DIR=tmp_path / "charts",
        TELEGRAM_BOT_TOKEN="",
        WATCHLIST_SYMBOLS="BTCUSDT,ETHUSDT,SOLUSDT",
        WATCH_INTERVAL_SECONDS=3600,
        SCAN_INTERVAL_SECONDS=3600,
    )
    return s


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture()
def source(settings) -> DemoMarketSource:
    return DemoMarketSource(settings)


@pytest.fixture()
def sample_df(source) -> "object":
    async def _get(symbol: str = "BTCUSDT", timeframe: str = "15m", limit: int = 300):
        return await source.get_klines(symbol, timeframe, limit)

    return _get


@pytest.fixture()
def analyzed_df(sample_df):
    """DataFrame с индикаторами (синхронная обёртка)."""
    async def _get(symbol: str = "BTCUSDT", timeframe: str = "15m", limit: int = 300):
        df = await sample_df(symbol, timeframe, limit)
        return compute_all(df)

    return _get


def run(coro):
    """Запуск корутины в свежем event loop (для синхронных тестов)."""
    return asyncio.run(coro)
