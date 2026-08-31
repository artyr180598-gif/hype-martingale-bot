"""Tests for the HYPE platform layer: auth, callbacks, settings, cache,
market overview, structure-anchored levels, publish validation, backtest
breakdowns and HTTP rate-limit retry."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from v3.telegram import V3Core

import httpx
import numpy as np
import pandas as pd
import pytest

from src.data.collector import _Http
from src.data.models import FundingEntry, GlobalStats, OrderBook, Ticker
from v3.config import SignalConfig, validate_config
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.models import TradingSignal
from v3.publisher import sanitize_for_publish
from v3.store import SignalLifecycle, SignalStore
from v3.tg.settings import UserSettingsService


def make_df(n: int = 200, start: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rets = rng.normal(0.0009, 0.0015, n)
    close = start * np.cumprod(1 + rets)
    ts = np.arange(n, dtype=np.int64) * 900_000 + 1_700_000_000_000
    opens = close * (1 + rng.normal(0, 0.0005, n))
    highs = np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.001, n)))
    lows = np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.001, n)))
    vol = np.full(n, 1000.0) * (1 + rng.normal(0, 0.1, n))
    return pd.DataFrame({"ts": ts, "open": opens, "high": highs, "low": lows, "close": close, "volume": vol})


class FakeExchange:
    """Minimal MarketDataSource-compatible fake used by the service tests."""

    is_demo = False
    mode = "fake"
    name = "fake"
    settings = SimpleNamespace(HTTP_TIMEOUT_SECONDS=12.0, HTTP_MAX_RETRIES=3)

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.oi = 50_000_000.0

    async def probe(self) -> str:
        return "fake"

    async def close(self) -> None:
        return None

    async def discover_instruments(self, category: str = "linear") -> list:
        return []

    async def get_klines(self, symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
        self.calls.append("klines")
        return make_df(min(limit, 200))

    async def get_history(self, symbol: str, timeframe: str = "1h", bars: int = 1000, max_requests: int = 40) -> pd.DataFrame:
        self.calls.append("history")
        return make_df(min(bars, 200))

    async def get_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        self.calls.append("tickers")
        syms = [s.upper() for s in symbols] if symbols else ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        out = []
        for i, s in enumerate(syms):
            out.append(
                Ticker(
                    symbol=s,
                    last=100.0 + i,
                    price_24h_pct=(1.0 if s == "BTCUSDT" else 2.0),
                    turnover_24h=1e9 - i * 1e8,
                    volume_24h=1e6,
                    high_24h=110.0,
                    low_24h=90.0,
                    open_24h=99.0,
                    bid=99.99,
                    ask=100.01,
                    funding_rate=0.0001,
                    open_interest_usd=self.oi,
                    mark_price=100.5,
                    index_price=99.5,
                    ts_ms=int(time.time() * 1000),
                )
            )
        return out

    async def get_account_ratio(self, symbol: str) -> float | None:
        self.calls.append("lsr")
        return 0.62

    async def get_funding(self, symbol: str, limit: int = 12) -> list[FundingEntry]:
        self.calls.append("funding")
        return [FundingEntry(ts_ms=1_700_000_000_000 + i * 8 * 3600_000, rate=0.0001, symbol=symbol.upper()) for i in range(limit)]

    async def get_recent_liquidations(self, limit: int = 200) -> list:
        self.calls.append("liquidations")
        return []

    async def get_orderbook(self, symbol: str, depth: int = 25) -> OrderBook:
        self.calls.append("book")
        return OrderBook(
            symbol=symbol.upper(),
            bids=[(100.0 - i * 0.01, 100) for i in range(1, depth + 1)],
            asks=[(100.0 + i * 0.01, 100) for i in range(1, depth + 1)],
            ts_ms=int(time.time() * 1000),
        )

    async def get_spot_movers(self, limit: int = 25) -> list:
        return []

    async def get_trending(self, limit: int = 12) -> list:
        return []

    async def get_fear_greed(self):
        from src.data.models import FearGreed

        return FearGreed(value=55, classification="Greed")

    async def get_global_stats(self) -> GlobalStats:
        return GlobalStats(
            total_market_cap_usd=2e12,
            total_volume_24h_usd=1e11,
            btc_dominance=51.0,
            eth_dominance=18.0,
            market_cap_change_24h_pct=1.2,
        )

    async def get_news(self, limit: int = 20) -> list:
        return []


def make_service(exchange: FakeExchange | None = None) -> tuple[FuturesDataService, FakeExchange]:
    ex = exchange or FakeExchange()
    svc = FuturesDataService(ex, SignalConfig())  # type: ignore[arg-type]
    return svc, ex


# ── config ──────────────────────────────────────────────────────
def test_config_allowed_user_ids_and_validation(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", " 5, 99,abc ")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "42")
    cfg = SignalConfig()
    assert cfg.allowed_user_ids == [5, 99, 42]
    errors = validate_config(cfg)
    assert any("non-numeric" in e for e in errors)

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "5,99")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "")
    cfg = SignalConfig()
    assert cfg.allowed_user_ids == [5, 99]
    assert validate_config(cfg) == []


def test_config_validation_bad_timeframes(monkeypatch):
    monkeypatch.setenv("TIMEFRAMES", "1m,bogus")
    cfg = SignalConfig()
    errors = validate_config(cfg)
    assert any("unsupported" in e for e in errors)


# ── HTTP 429 retry ──────────────────────────────────────────────
def test_http_retries_429_with_backoff():
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        if hits["n"] < 3:
            return httpx.Response(429, request=request, headers={"Retry-After": "0"})
        return httpx.Response(200, request=request, json={"ok": True})

    settings = SimpleNamespace(HTTP_TIMEOUT_SECONDS=12.0, HTTP_MAX_RETRIES=3)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http = _Http(settings, "https://example.test", client=client)  # type: ignore[arg-type]

    async def run() -> dict:
        try:
            return await http.get("/v5/test")
        finally:
            await http.close()

    assert asyncio.run(run()) == {"ok": True}
    assert hits["n"] == 3


# ── data service: history + overview ────────────────────────────
def test_bundle_builds_parallel_and_tracks_oi():
    ex = FakeExchange()
    cfg = SignalConfig(TICKER_CACHE_TTL_SECONDS=0.0)
    svc = FuturesDataService(ex, cfg)  # type: ignore[arg-type]

    async def run():
        b1 = await svc.build_bundle("BTCUSDT")
        ex.oi = 55_000_000.0
        time.sleep(0.001)
        b2 = await svc.build_bundle("BTCUSDT")
        ov = await svc.market_overview()
        return b1, b2, ov

    b1, b2, ov = asyncio.run(run())
    assert b1.symbol == "BTCUSDT"
    assert b1.open_interest_usd == 50_000_000.0
    assert b1.open_interest_history, "OI history must be populated"
    assert b2.oi_change_24h_pct == pytest.approx(10.0, rel=0.1)
    assert b1.price_24h_pct == 1.0
    assert b1.long_short_ratio == pytest.approx(0.62)
    assert b1.mark_price == pytest.approx(100.5)
    assert b1.index_price == pytest.approx(99.5)
    assert "lsr" in ex.calls, "deep bundle must fetch the Bybit account ratio"
    assert b1.data_age_seconds is not None and b1.data_age_seconds < 5

    # derivatives snapshot must carry the ratio + mark/index into the report
    from v3.analysis.derivatives import analyze_derivatives

    der = analyze_derivatives(b1, cfg)
    assert der.long_short_ratio == pytest.approx(0.62)
    assert der.mark_price == pytest.approx(100.5)
    assert "LS ratio" in der.note
    assert der.taker_buy_sell_ratio == pytest.approx(0.62)
    for key in ("btc", "eth", "global", "gainers", "losers", "top_turnover", "universe_count", "avg_move_24h_pct"):
        assert key in ov
    assert ov["universe_count"] >= 3


def test_ticker_cache_hits_within_ttl():
    ex = FakeExchange()
    svc, _ = make_service(ex)
    asyncio.run(svc.tickers(["BTCUSDT"]))
    tcalls = ex.calls.count("tickers")
    asyncio.run(svc.tickers(["BTCUSDT"]))
    assert ex.calls.count("tickers") == tcalls, "second call must come from cache"
    svc._invalidate("tickers:")
    asyncio.run(svc.tickers(["BTCUSDT"]))
    assert ex.calls.count("tickers") == tcalls + 1


def test_light_bundle_skips_news():
    ex = FakeExchange()
    svc, _ = make_service(ex)
    asyncio.run(svc.build_bundle("BTCUSDT", deep=False))
    assert "book" in ex.calls
    assert "lsr" not in ex.calls, "light scan must not fetch the account ratio"


def test_bybit_account_ratio_uses_public_endpoint():
    from src.config.settings import Settings
    from src.data.collector import BybitSource

    src = BybitSource(Settings())
    calls: list[str] = []

    async def fake_get(path: str, params: dict | None = None):  # noqa: ANN001
        calls.append(path)
        return {"retCode": 0, "result": {"list": [{"buyRatio": "0.5833"}]}}

    src.get = fake_get  # type: ignore[method-assign]

    async def run() -> tuple[float, float]:
        first = await src.get_account_ratio("BTCUSDT")
        second = await src.get_account_ratio("BTCUSDT")
        return float(first), float(second)

    first, second = asyncio.run(run())
    assert first == pytest.approx(0.5833)
    assert second == pytest.approx(0.5833)
    assert calls == ["/v5/market/account-ratio"], "second call must hit the 300s cache"


# ── levels: structure-anchored entry zone ──────────────────────
def test_levels_anchors_entry_zone_to_support():
    from v3.analysis.levels import build_levels
    from v3.analysis.timeframes import build_timeframe_view
    from v3.config import SignalConfig

    view = build_timeframe_view(make_df(200), "15m")
    # force a support 0.3 ATR below price
    atr = view.atr
    support = 100.0 - 0.3 * atr
    view.support = support
    levels = build_levels("LONG", 100.0, atr, view, SignalConfig())
    assert levels is not None
    assert levels.entry_zone[0] == pytest.approx(support, rel=1e-6)
    assert levels.entry_zone[1] == pytest.approx(100.0, rel=1e-6)
    assert any("anchored to support" in w for w in levels.why)


# ── backtest breakdowns ─────────────────────────────────────────
def test_backtest_metrics_regime_and_direction_breakdown():
    from v3.backtest import BacktestTrade, metrics_from_trades

    def trade(direction: str, regime: str, r: float) -> BacktestTrade:
        return BacktestTrade(
            signal={"direction": direction, "features": {"regime": {"regime": regime}}, "ts_ms": 1},
            entry_ts=1, exit_ts=2, entry_price=100, exit_price=100, direction=direction,
            rr=2, r_multiple=r, pnl_pct=0, bars_held=1, exit_reason="TP", score=80, confidence=0.9,
        )

    trades = [trade("LONG", "TRENDING_UP", 1.0), trade("SHORT", "RANGING", -1.0)]
    m = metrics_from_trades(trades, signals_generated=2)
    assert m["by_direction"]["LONG"]["trades"] == 1
    assert m["by_regime"]["TRENDING_UP"]["expectancy_r"] == 1.0
    assert m["by_regime"]["RANGING"]["win_rate"] == 0.0


# ── publisher ───────────────────────────────────────────────────
def test_publisher_downgrades_invalid_signal_keeps_reasons():
    cfg = SignalConfig()
    sig = TradingSignal(
        uid="p1", symbol="X", ts_ms=1, direction="LONG", status="CONFIRMED", score=90,
        confidence=0.1, quality=90, tier="S", rr=2, risk_score=3, price=100,
        entry_zone=(99, 100), stop_loss=98, targets=[102, 104, 106],
    )
    out, violations = sanitize_for_publish(sig, cfg)
    assert out.direction == "NO_TRADE"
    assert any("confidence" in v for v in violations)
    assert any("confidence" in r for r in out.no_trade_reasons)

    good = TradingSignal(
        uid="p2", symbol="Y", ts_ms=1, direction="LONG", status="CONFIRMED", score=90,
        confidence=0.9, quality=90, tier="S", rr=2, risk_score=3, price=100,
        entry_zone=(99, 100), stop_loss=98, targets=[102, 104, 106],
    )
    out2, v2 = sanitize_for_publish(good, cfg)
    assert out2.direction == "LONG" and v2 == []

    # stale / too-old data must never pass the publish gate, even if the
    # engine marked the signal as confirmed (defence in depth).
    stale = TradingSignal(
        uid="p3", symbol="Z", ts_ms=1, direction="LONG", status="CONFIRMED", score=90,
        confidence=0.9, quality=90, tier="S", rr=2, risk_score=3, price=100,
        entry_zone=(99, 100), stop_loss=98, targets=[102, 104, 106],
        data_age_seconds=9999, stale=True,
    )
    out3, v3_ = sanitize_for_publish(stale, cfg)
    assert out3.direction == "NO_TRADE"
    assert any("stale" in v for v in v3_)

    # age beyond the TTL is caught even when the stale flag was not set.
    no_flag = TradingSignal(
        uid="p4", symbol="Z", ts_ms=1, direction="LONG", status="CONFIRMED", score=90,
        confidence=0.9, quality=90, tier="S", rr=2, risk_score=3, price=100,
        entry_zone=(99, 100), stop_loss=98, targets=[102, 104, 106],
        data_age_seconds=cfg.MAX_DATA_AGE_SECONDS + 1, stale=False,
    )
    out4, v4 = sanitize_for_publish(no_flag, cfg)
    assert out4.direction == "NO_TRADE"
    assert any("stale" in v for v in v4)


# ── Telegram: auth + callbacks + settings ───────────────────────
def _core_for(cfg: SignalConfig, allowlist_env: str = "") -> "V3Core":
    from v3.telegram import V3Core

    store = SignalStore("/tmp/v3_test_platform.db")
    lifecycle = SignalLifecycle(store, cooldown_seconds=60, max_active=3)
    dummy = type("D", (), {"mode": "auto"})()  # type: ignore[assignment]
    return V3Core(dummy, None, store, lifecycle, cfg)  # type: ignore[arg-type]


def test_telegram_authorization_deny_by_default(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_ID", raising=False)
    core = _core_for(SignalConfig())
    assert "НЕТ ДОСТУПА" in asyncio.run(core.handle_message("help", None, 999))
    # legacy no-user path stays open for CLI/tests
    assert "help" not in asyncio.run(core.handle_message("help", None, None)).lower().split()[0]


def test_telegram_authorization_allows_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    core = _core_for(SignalConfig())
    assert asyncio.run(core.handle_message("help", None, 42)) != core.access_denied_text
    assert "НЕТ ДОСТУПА" in asyncio.run(core.handle_message("help", None, 43))
    core.store.close()


def test_telegram_callbacks_menu_glossary_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    core = _core_for(SignalConfig())
    reply = asyncio.run(core.handle_callback("menu", 42))
    assert "HYPE" in reply.text and reply.keyboard is not None
    reply = asyncio.run(core.handle_callback("glossary:rsi", 42))
    assert "RSI" in reply.text
    reply = asyncio.run(core.handle_callback("list:top:0", 42))
    assert "Сейчас нет подходящих сетапов" in reply.text
    reply = asyncio.run(core.handle_callback("settings", 42))
    assert "НАСТРОЙКИ" in reply.text
    reply = asyncio.run(core.handle_callback("set:deposit:500", 42))
    assert "$500" in reply.text
    reply = asyncio.run(core.handle_callback("set:risk:2", 42))
    assert "2%" in reply.text
    core.store.close()


def test_user_settings_roundtrip_and_bounds():
    store = SignalStore("/tmp/v3_test_settings.db")
    svc = UserSettingsService(store, SignalConfig())
    s = svc.apply(1, "deposit_usd", "99999")
    assert s.deposit_usd == 99999.0
    svc.apply(1, "risk_per_trade_pct", "0.1")
    s2 = svc.get(1)
    assert s2.risk_per_trade_pct == 0.1
    svc.apply(1, "risk_per_trade_pct", "999")
    assert svc.get(1).risk_per_trade_pct <= 5.0
    svc.apply(1, "mode", "pro")
    assert svc.get(1).mode == "pro"
    store.close()


def test_telegram_deposit_message_flow(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    core = _core_for(SignalConfig())
    asyncio.run(core.handle_callback("dep_custom", 42))
    reply = asyncio.run(core.handle_message("2500", None, 42))
    assert "$2,500" in reply
    assert "Депозит" in core.settings_text(42)
    core.store.close()


# ── scanner setup filtering ─────────────────────────────────────
def test_scanner_best_setups_filters_direction_and_quality():
    import time as _time

    from v3.scanner import Scanner

    class FakeEngine:
        class FakeData:
            mode = "fake"
            is_demo = False

        data = FakeData()

        async def analyze_batch(self, symbols, concurrency=4, **kwargs):
            return [
                TradingSignal(uid=f"u{i}", symbol=s, ts_ms=int(_time.time() * 1000), direction=dirn,
                              status="CONFIRMED", score=q, confidence=0.9, quality=q, tier="A",
                              rr=2.0, risk_score=3, price=100, entry_zone=(99, 100),
                              stop_loss=98, targets=[102, 104, 106])
                for i, (s, dirn, q) in enumerate([
                    ("LONGUSDT", "LONG", 85.0),
                    ("SHORTUSDT", "SHORT", 80.0),
                    ("WEAKUSDT", "LONG", 60.0),
                ])
            ]

    class T:
        def __init__(self, sym, turnover):
            self.symbol = sym
            self.turnover_24h = turnover
            self.volume_24h = turnover
            self.last = 100.0
            self.price_24h_pct = 5.0
            self.high_24h = 105.0
            self.low_24h = 95.0
            self.bid = 99.99
            self.ask = 100.01
            self.funding_rate = 0.0001
            self.open_interest_usd = 1e6
            self.open_interest = 1000.0

    tickers = {s: T(s, 100_000_000.0) for s in ["LONGUSDT", "SHORTUSDT", "WEAKUSDT"]}
    scanner = Scanner(FakeEngine(), SignalConfig())  # type: ignore[arg-type]
    result = asyncio.run(scanner.run(tickers, limit=10, top=3))
    assert len(result.analyzed) == 3
    longs = scanner.best_setups("LONG")
    assert [i["signal"].symbol for i in longs] == ["LONGUSDT"]
    assert len(scanner.best_setups()) == 2  # weak filtered by SCAN_SHOW_QUALITY_MIN
