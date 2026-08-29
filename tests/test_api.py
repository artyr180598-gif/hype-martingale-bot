"""Тесты FastAPI (через TestClient, демо-режим)."""

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.config.settings import Settings
from src.core.context import AppContext, get_context


@pytest.fixture()
def api_settings(tmp_path, monkeypatch) -> Settings:
    s = Settings(
        _env_file=None,
        MARKET_DATA_MODE="demo",
        DATA_DIR=tmp_path,
        DB_PATH=tmp_path / "api.db",
        CHART_DIR=tmp_path / "charts",
        TELEGRAM_BOT_TOKEN="",
        WATCHLIST_SYMBOLS="BTCUSDT,ETHUSDT",
    )
    # подменяем глобальный контекст на тестовый
    from src.core import context as ctx_mod

    test_ctx = AppContext()
    test_ctx.settings = s
    monkeypatch.setattr(ctx_mod, "ctx", test_ctx)
    test_ctx.ensure_services()
    return s


def test_health(api_settings):
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_status(api_settings):
    client = TestClient(app)
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "demo"
    assert "BTCUSDT" in body["watchlist"]


def test_watch_add_and_signal(api_settings):
    client = TestClient(app)
    r = client.post("/api/watch", json={"symbol": "PEPEUSDT"})
    assert r.status_code == 200
    assert "PEPEUSDT" in r.json()["watchlist"]
    r = client.get("/api/watch/PEPEUSDT")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "PEPEUSDT"
    assert 0 <= body["score"] <= 100


def test_signal_endpoint(api_settings):
    client = TestClient(app)
    r = client.get("/api/signal/SOLUSDT")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "SOLUSDT"
    assert "plan" in body


def test_chart_png(api_settings):
    client = TestClient(app)
    r = client.get("/api/chart/BTCUSDT")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_unknown_symbol_404(api_settings):
    client = TestClient(app)
    r = client.get("/api/signal/FAKECOINUSDT")
    assert r.status_code in (404, 500)


def test_dashboard_html(api_settings):
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "HYPE Advisor" in r.text


def test_scan_endpoint(api_settings):
    client = TestClient(app)
    r = client.post("/api/scan", params={"deep_top": 4})
    assert r.status_code == 200
    body = r.json()
    assert body["analyzed"] <= 4
    assert body["total_instruments"] > 0
    r2 = client.get("/api/scan")
    assert r2.status_code == 200


def test_spectrum_endpoint(api_settings):
    client = TestClient(app)
    r = client.get("/api/spectrum/SOLUSDT")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "SOLUSDT"
    assert body["direction"] in {"LONG", "SHORT", "WAIT"}
    assert len(body["timeframes"]) >= 3
    assert "group_scores" in body and "factors" in body


def test_trade_card_endpoint(api_settings):
    client = TestClient(app)
    r = client.get("/api/trade-card/SOLUSDT", params={"deposit": 500, "risk_pct": 1, "exchange": "bybit"})
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "SOLUSDT"
    assert body["exchange"] == "bybit"
    assert isinstance(body["steps"], list)
    if body["side"] in ("LONG", "SHORT"):
        assert body["deposit_usd"] == 500
        assert len(body["steps"]) >= 8


def test_spectrum_chart_png(api_settings):
    client = TestClient(app)
    r = client.get("/api/spectrum-chart/BTCUSDT")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
