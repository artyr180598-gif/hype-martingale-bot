"""Тесты инварианта «только реальные данные» (раунд 3).

Платформа НИКОГДА не подменяет отсутствующие данные синтетикой:
без тикера/свечей — NO TRADE и честные причины, без потока ликвидаций — «н/д».,
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import aiohttp
import numpy as np
import pandas as pd
import pytest

from src.config.settings import Settings
from src.data.collector import BybitSource, DataSourceError, FailoverSource, build_source
from src.data.liquidations_ws import (
    BybitLiquidationStream,
    LiquidationBuffer,
    parse_liquidation_message,
)
from v3.config import SignalConfig
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.models import DataBundle, TradingSignal
from v3.validator import validate_for_publish


def _tf_map(direction: str = "up", n: int = 300) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(7)
    out: dict[str, pd.DataFrame] = {}
    for tf, tf_ms in [("1m", 60_000), ("5m", 300_000), ("15m", 900_000), ("1h", 3_600_000), ("4h", 14_400_000)]:
        ret = rng.normal(0, 0.0015, n) + (0.0009 if direction == "up" else -0.0009)
        close = 100.0 * np.cumprod(1 + ret)
        ts_end = int(time.time() * 1000)
        ts = (ts_end - np.arange(n - 1, -1, -1) * tf_ms).astype(np.int64)
        out[tf] = pd.DataFrame({
            "ts": ts, "open": close, "high": close * 1.001,
            "low": close * 0.999, "close": close, "volume": np.full(n, 1000.0),
        })
    return out


def _bundle(**kw) -> DataBundle:
    base = dict(
        symbol="REALTESTUSDT",
        ts_ms=int(time.time() * 1000),
        price=100.0,
        price_24h_pct=1.2,
        turnover_24h=100_000_000.0,
        volume_24h=1_000_000.0,
        spread_pct=0.05,
        funding_rate=0.0001,
        funding_history=[0.0001, 0.0001],
        open_interest_usd=50_000_000.0,
        orderbook={
            "bids": [(99.98 + i * 0.01, 100) for i in range(20)],
            "asks": [(100.02 + i * 0.01, 100) for i in range(20)],
            "ts_ms": int(time.time() * 1000),
        },
        btc_price_24h_pct=1.0,
        btc_turnover_24h=20_000_000_000.0,
        btc_dominance=55.0,
        global_change_pct=1.0,
        degraded=[],
        data_age_seconds=2.0,  # реальный возраст от биржевого timestamp
    )
    base.update(kw)
    return DataBundle(**base)


# ════════════════════════════════════════════════════════════════
#  конфигурация: demo удалён
# ════════════════════════════════════════════════════════════════
def test_market_data_mode_default_is_live(monkeypatch):
    monkeypatch.delenv("MARKET_DATA_MODE", raising=False)
    assert SignalConfig().MARKET_DATA_MODE == "live"
    assert Settings().MARKET_DATA_MODE == "live"


def test_demo_mode_is_startup_validation_error(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_MODE", "demo")
    with pytest.raises(Exception) as excinfo:
        SignalConfig()
    assert "Режим MARKET_DATA_MODE=demo удалён" in str(excinfo.value)

    settings = Settings()
    with pytest.raises(DataSourceError) as err:
        build_source(settings)
    assert "demo удалён" in str(err.value)


def test_unknown_mode_is_startup_validation_error(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_MODE", "paper")
    with pytest.raises(Exception) as excinfo:
        SignalConfig()
    assert "live | auto" in str(excinfo.value)


def test_build_source_instantiates_real_exchanges_only(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_MODE", "live")
    source, mode = build_source(Settings())
    assert mode == "live"
    assert isinstance(source, FailoverSource)
    assert {d.name for d in source._delegates} == {"bybit", "binance", "mexc"}
    assert not hasattr(__import__("src.data.collector", fromlist=["X"]), "DemoMarketSource")


# ════════════════════════════════════════════════════════════════
#  fail-closed: нет реальных данных — нет «успешного» анализа
# ════════════════════════════════════════════════════════════════
def test_engine_blocks_when_no_timeframe_data():
    cfg = SignalConfig()
    engine = FuturesSignalEngine(None, cfg)  # type: ignore[arg-type]
    sig = engine.evaluate_bundle(_bundle(), {}, btc_tf=None)
    assert sig.direction == "NO_TRADE"
    assert sig.features.get("no_data") is True
    assert any("no usable timeframe" in r for r in sig.no_trade_reasons)


def test_engine_blocks_missing_exchange_timestamp():
    """Тикер без биржевого timestamp → возраста нет → сигнал запрещён."""
    cfg = SignalConfig()
    engine = FuturesSignalEngine(None, cfg)  # type: ignore[arg-type]
    bundle = _bundle(data_age_seconds=None)
    sig = engine.evaluate_bundle(bundle, _tf_map(), btc_tf=None)
    assert sig.direction == "NO_TRADE"
    assert sig.features.get("no_data") is True
    assert any("no real market data" in r for r in sig.no_trade_reasons)


def test_engine_blocks_missing_ticker():
    cfg = SignalConfig()
    engine = FuturesSignalEngine(None, cfg)  # type: ignore[arg-type]
    bundle = _bundle(price=0.0)
    sig = engine.evaluate_bundle(bundle, _tf_map(), btc_tf=None)
    assert sig.direction == "NO_TRADE"
    assert sig.features.get("no_data") is True


def test_validator_rejects_signal_without_exchange_timestamp():
    cfg = SignalConfig()
    sig = TradingSignal(
        uid="rd1", symbol="REALTESTUSDT", ts_ms=int(time.time() * 1000),
        direction="LONG", status="CONFIRMED", score=90, quality=90, confidence=0.9,
        rr=2.0, risk_score=3, price=100, entry_zone=(99, 100), stop_loss=98,
        targets=[102, 104],
    )
    ok, why = validate_for_publish(sig, cfg)
    assert not ok
    assert any("no real market data" in w for w in why)

    sig.data_age_seconds = 1.5
    ok2, _ = validate_for_publish(sig, cfg)
    assert ok2


def test_signal_dict_contains_source_but_no_demo_flag():
    sig = TradingSignal(uid="rd2", symbol="X", ts_ms=1, direction="WAIT", source="bybit")
    d = sig.to_dict()
    assert "is_demo" not in d
    assert d["source"] == "bybit"


def test_no_demonstration_imports_remain():
    import src.data.collector as collector

    assert not hasattr(collector, "DemoMarketSource")
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.data.demo")


# ════════════════════════════════════════════════════════════════
#  реальные ликвидации Bybit WS
# ════════════════════════════════════════════════════════════════
def _liq_msg(symbol: str = "BTCUSDT", side: str = "Buy", size: float = 0.5,
             price: float = 65000.0, ts_ms: int | None = None) -> dict:
    return {
        "topic": f"liquidation.{symbol}",
        "type": "snapshot",
        "ts": ts_ms or int(time.time() * 1000),
        "data": {
            "updatedTime": ts_ms or int(time.time() * 1000),
            "symbol": symbol,
            "side": side,
            "size": str(size),
            "price": str(price),
        },
    }


def test_parse_liquidation_message_valid():
    events = parse_liquidation_message(_liq_msg())
    assert len(events) == 1
    ev = events[0]
    assert ev.symbol == "BTCUSDT"
    assert ev.side == "Buy"
    assert abs(ev.size - 0.5 * 65000.0) < 1e-6
    assert ev.ts_ms > 0


def test_parse_liquidation_message_rejects_service_frames():
    assert parse_liquidation_message({"op": "ping"}) == []
    assert parse_liquidation_message({"op": "pong", "ts": 1}) == []
    assert parse_liquidation_message({"success": True, "op": "subscribe"}) == []
    assert parse_liquidation_message({"topic": "kline.1.BTCUSDT", "data": {}}) == []


def test_parse_liquidation_message_rejects_malformed_rows():
    bad = dict(_liq_msg())
    bad["data"] = {"updatedTime": 0, "symbol": "", "side": "weird", "size": "0", "price": "0"}
    assert parse_liquidation_message(bad) == []
    bad2 = _liq_msg(side="long")  # невалидная сторона
    assert parse_liquidation_message(bad2) == []


def test_liquidation_buffer_symbol_filter_and_ttl():
    buf = LiquidationBuffer()
    now = int(time.time() * 1000)
    buf.add(parse_liquidation_message(_liq_msg("BTCUSDT", ts_ms=now - 60_000)))
    buf.add(parse_liquidation_message(_liq_msg("ETHUSDT", ts_ms=now - 5_000)))
    buf.add(parse_liquidation_message(_liq_msg("BTCUSDT", ts_ms=now - 2_000_000)))  # старое

    recent = buf.events("BTCUSDT", 900, now_ms=now)
    assert len(recent) == 1
    assert recent[0].symbol == "BTCUSDT"
    assert recent[0].ts_ms == now - 60_000

    removed = buf.purge_older_than(900, now_ms=now)
    assert removed == 1
    assert len(buf) == 2


class _FakeWS:
    """Поддельное WS-соединение, отдающее заготовленные реальные кадры Bybit."""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0)  # отдаём управление планировщику
        if not self.payloads:
            raise StopAsyncIteration
        payload = self.payloads.pop(0)

        class _Msg:
            type = aiohttp.WSMsgType.TEXT

            @staticmethod
            def json():
                return payload

        return _Msg()

    async def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, ws: _FakeWS) -> None:
        self.ws = ws
        self.closed = False

    def ws_connect(self, url: str, heartbeat: float | None = None):
        session = self

        class _CM:
            async def __aenter__(self):
                return session.ws

            async def __aexit__(self, *exc):
                return False

        return _CM()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False


async def test_liquidation_stream_ingests_real_events_via_fake_session():
    payloads = [
        {"op": "subscribe", "success": True},          # ответ подписки — не событие
        _liq_msg("BTCUSDT", side="Sell", size=1.2, price=65000.0),
        _liq_msg("DOGEUSDT", side="Buy", size=10.0, price=0.1),
        _liq_msg("BTCUSDT", side="Sell", size=0.8, price=65000.0),
    ]
    base = int(time.time() * 1000)
    payloads[1]["data"]["updatedTime"] = base
    payloads[2]["data"]["updatedTime"] = base + 1
    payloads[3]["data"]["updatedTime"] = base + 2
    fake_ws = _FakeWS(payloads)
    stream = BybitLiquidationStream(
        session_factory=lambda: _FakeSession(fake_ws),
        max_age_seconds=900.0,
    )
    try:
        await stream.start(["BTCUSDT", "DOGEUSDT"])
        for _ in range(50):
            if len(stream.buffer) >= 3:
                break
            await asyncio.sleep(0.02)
        events = stream.events("BTCUSDT")
        assert [round(e.qty, 3) for e in events] == [0.8, 1.2]
        assert all(e.symbol == "BTCUSDT" for e in events)
        assert {e.side for e in events} == {"Sell"}
        # подписка отправлена на реальные топики bybit v5
        assert any("liquidation.BTCUSDT" in m.get("args", []) for m in fake_ws.sent)
        status = stream.diagnostics()
        assert status["started"] is True
        assert status["buffered_events"] == 3
    finally:
        await stream.stop()
    assert stream.connected is False
    assert stream.healthy is False  # после остановки поток нездоров — потребители увидят «н/д»


class _FakeBybitSource:
    mode = "bybit"
    name = "bybit"
    settings = Settings()

    async def get_recent_liquidations(self, limit: int) -> list:
        return []

    async def close(self) -> None:
        return None


class _FakeStream:
    healthy = True
    last_error = ""

    def __init__(self, events):
        self._events = events

    def events(self, symbol: str, max_age_seconds: float) -> list:
        return self._events

    def diagnostics(self) -> dict:
        return {"healthy": self.healthy, "buffered_events": len(self._events)}


async def test_service_consumes_ws_liquidations_not_proxy():
    from src.data.models import Liquidation

    now = int(time.time() * 1000)
    liq = Liquidation(symbol="BTCUSDT", side="Buy", size=27_000.0, qty=2.0, price=13_500.0, ts_ms=now)
    service = FuturesDataService(source=_FakeBybitSource(), cfg=SignalConfig())
    service._liq_stream = _FakeStream([liq])

    rows = await service.liquidations("BTCUSDT", 10)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["ts_ms"] == now       # биржевой timestamp сохранён
    assert rows[0]["side"] == "Buy"

    # без потока — честный пустой список (дальше «н/д»), без прокси крупных сделок
    service._liq_stream = None
    rows = await service.liquidations("BTCUSDT", 10)
    assert rows == []


async def test_bybit_rest_liquidations_is_honest_empty():
    rows = await BybitSource(Settings()).get_recent_liquidations(10)
    assert rows == []  # у Bybit нет публичного REST-фида ликвидаций; прокси удалён


async def test_failover_reports_per_source_diagnostics():
    settings = Settings()
    settings.HTTP_MAX_RETRIES = 0
    source = FailoverSource(settings, [BybitSource(settings), _FakeBybitSource()])
    try:
        with pytest.raises(DataSourceError):
            await source.probe()
        diag = {row["source"]: row for row in source.diagnostics()}
        assert "bybit" in diag and "bybit" in source.failures.keys() | {"bybit"}
        assert diag["bybit"]["consecutive_errors"] >= 1
        assert diag["bybit"]["last_error"]
    finally:
        await source.close()
