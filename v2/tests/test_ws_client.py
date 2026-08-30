"""
WebSocket-слой: агрегация пятиминутного окна и разбор текстовых запросов.

Само соединение здесь не поднимается (нет сети), но логика окна — именно то,
что кормит метриками уровень 1 сканера, поэтому проверяется отдельно.
"""

from __future__ import annotations

import time

from v2.bot import extract_query
from v2.data.ws_client import TickerAggregator


async def test_aggregator_sums_trades_in_window():
    agg = TickerAggregator(window_sec=300)
    await agg.add_trade("BTCUSDT", 120_000.0, 1)
    await agg.add_trade("BTCUSDT", 80_000.0, 1)
    volume, trades = await agg.snapshot("BTCUSDT")
    assert volume == 200_000.0
    assert trades == 2


async def test_aggregator_drops_old_events():
    agg = TickerAggregator(window_sec=300)
    agg._events["ETHUSDT"] = [(time.time() - 600, 1000.0, 5)]   # старше окна
    await agg.add_trade("ETHUSDT", 500.0, 1)
    volume, trades = await agg.snapshot("ETHUSDT")
    assert volume == 500.0
    assert trades == 1


async def test_aggregator_all_snapshots():
    agg = TickerAggregator(window_sec=300)
    await agg.add_trade("AAA", 10.0, 1)
    await agg.add_trade("BBB", 20.0, 2)
    snapshots = await agg.all_snapshots()
    assert set(snapshots) == {"AAA", "BBB"}
    assert snapshots["BBB"] == (20.0, 2)


async def test_snapshot_of_unknown_symbol_is_zero():
    agg = TickerAggregator(window_sec=300)
    assert await agg.snapshot("NOPE") == (0.0, 0)


async def test_snapshot_replaces_aggregated_feed():
    """Поток Bybit tickers отдаёт готовое окно — оно заменяет накопленное."""
    agg = TickerAggregator(window_sec=300)
    await agg.add_trade("SOLUSDT", 100.0, 1)
    await agg.add_snapshot("SOLUSDT", 250_000.0, 430)
    assert await agg.snapshot("SOLUSDT") == (250_000.0, 430)


# ═══════════════════════════════════════════════════════════════
#  РАЗБОР ПОЛЬЗОВАТЕЛЬСКОГО ЗАПРОСА
# ═══════════════════════════════════════════════════════════════
def test_extract_evm_address():
    text = "проанализируй 0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984 пожалуйста"
    assert extract_query(text) == "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984"


def test_extract_symbol_from_phrase():
    assert extract_query("разбери AURORA") == "AURORA"
    assert extract_query("что думаешь про KELP?") == "KELP"


def test_extract_symbol_strips_exchange_suffix():
    assert extract_query("проанализируй SOLUSDT") == "SOL"


def test_extract_quoted_symbol():
    assert extract_query('посмотри "Titan Layer2"') == "TITAN LAYER2"


def test_extract_ignores_stop_words():
    assert extract_query("проанализируй токен, пожалуйста") == ""
    assert extract_query("") == ""
