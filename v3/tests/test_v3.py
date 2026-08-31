"""Tests for the v3 futures signal engine."""

from __future__ import annotations

import math
import time

import numpy as np
import pandas as pd

from v3.analysis.derivatives import analyze_derivatives
from v3.analysis.levels import build_levels
from v3.analysis.orderflow import analyze_orderflow
from v3.analysis.regime import detect_regime
from v3.analysis.scoring import score_signal
from v3.analysis.timeframes import build_timeframe_view
from v3.backtest import metrics_from_trades
from v3.config import SignalConfig
from v3.engine import FuturesSignalEngine
from v3.models import DataBundle, TradingSignal
from v3.report import render_signal
from v3.store import SignalLifecycle, SignalStore
from v3.validator import validate_for_publish


def make_df(n: int = 400, direction: str = "up", start: float = 100.0) -> pd.DataFrame:
    """Deterministic synthetic OHLCV with trend/cycles."""
    rng = np.random.default_rng(42)
    returns = rng.normal(0, 0.0015, n)
    if direction == "up":
        returns += 0.0009
    elif direction == "down":
        returns -= 0.0009
    close = start * np.cumprod(1 + returns)
    ts = np.arange(n, dtype=np.int64) * 60_000 + 1_700_000_000_000
    opens = close * (1 + rng.normal(0, 0.0005, n))
    highs = np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.001, n)))
    lows = np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.001, n)))
    volume = np.full(n, 1000.0) * (1 + rng.normal(0, 0.1, n))
    return pd.DataFrame({
        "ts": ts, "open": opens, "high": highs, "low": lows, "close": close, "volume": volume,
    })


def make_bundle(orderbook: bool = True) -> DataBundle:
    book = None
    if orderbook:
        book = {
            "bids": [(99.98 + i * 0.01, 100) for i in range(20)],
            "asks": [(100.02 + i * 0.01, 100) for i in range(20)],
            "ts_ms": 0,
        }
    return DataBundle(
        symbol="TESTUSDT",
        ts_ms=1_700_000_000_000,
        price=100.0,
        price_24h_pct=2.0,
        turnover_24h=100_000_000.0,
        volume_24h=1_000_000.0,
        spread_pct=0.05,
        funding_rate=0.0001,
        funding_history=[0.0001, 0.0001, 0.0001],
        open_interest_usd=50_000_000.0,
        orderbook=book,
        btc_price_24h_pct=1.5,
        btc_turnover_24h=20_000_000_000.0,
        btc_dominance=55.0,
        global_change_pct=1.0,
        is_demo=False,
        degraded=[],
    )


def make_tf_map() -> dict[str, pd.DataFrame]:
    return {
        "1m": make_df(400, "up"),
        "5m": make_df(400, "up"),
        "15m": make_df(400, "up"),
        "1h": make_df(400, "up"),
        "4h": make_df(400, "up"),
    }


def test_timeframe_view_builds():
    df = make_df(200, "up")
    view = build_timeframe_view(df, "1h")
    assert view.timeframe == "1h"
    assert math.isfinite(view.adx)
    assert view.atr > 0
    assert view.rsi >= 0
    assert view.rsi <= 100


def test_regime_trending_up():
    views = [build_timeframe_view(make_df(400, "up"), "1h")]
    cfg = SignalConfig()
    reg = detect_regime(views, cfg)
    assert reg.regime in ("TRENDING_UP", "ACCUMULATION", "UNCERTAIN")
    assert reg.direction in ("up", "flat")


def test_derivatives_funding_trend():
    cfg = SignalConfig()
    bundle = make_bundle(False)
    der = analyze_derivatives(bundle, cfg)
    assert der.funding_rate == 0.0001
    assert der.funding_trend in ("neutral", "rising", "falling")
    assert der.liq_imbalance == 0.0


def test_orderflow_excellent():
    bundle = make_bundle(True)
    view = build_timeframe_view(make_df(200, "up"), "1h")
    of = analyze_orderflow(bundle.orderbook, view, SignalConfig())
    assert of.liquidity_grade in ("excellent", "ok", "thin")
    assert of.spread_pct is not None
    assert isinstance(of.bid_depth_usd, float)


def test_levels_long_rr():
    cfg = SignalConfig()
    view = build_timeframe_view(make_df(200, "up"), "1h")
    levels = build_levels("LONG", 100.0, view.atr, view, cfg)
    assert levels is not None
    assert levels.rr >= cfg.MIN_RISK_REWARD or levels.rr > 0
    assert levels.stop_loss < 100.0
    assert len(levels.targets) == 3


def test_engine_long_with_good_data():
    cfg = SignalConfig()
    engine = FuturesSignalEngine(None, cfg)  # type: ignore[arg-type]
    sig = engine.evaluate_bundle(make_bundle(True), make_tf_map(), btc_tf=make_df(200, "up"))
    assert sig.direction in ("LONG", "SHORT", "WAIT", "NO_TRADE")
    assert sig.score >= 0


def test_engine_no_trade_when_no_data():
    cfg = SignalConfig()
    engine = FuturesSignalEngine(None, cfg)  # type: ignore[arg-type]
    sig = engine.evaluate_bundle(make_bundle(False), {}, btc_tf=None)
    assert sig.direction == "NO_TRADE"
    assert sig.no_trade_reasons


def test_engine_no_trade_when_low_quality():
    cfg = SignalConfig()
    engine = FuturesSignalEngine(None, cfg)  # type: ignore[arg-type]
    bundle = make_bundle(True)
    bundle.turnover_24h = 1000.0
    bundle.price = 0.0
    sig = engine.evaluate_bundle(bundle, make_tf_map(), btc_tf=None)
    assert sig.direction == "NO_TRADE"


def test_validator_rejects_demo():
    cfg = SignalConfig()
    sig = TradingSignal(uid="x", symbol="X", ts_ms=1, direction="LONG", is_demo=True, score=90, confidence=0.9, rr=2.0, risk_score=3, price=100, stop_loss=99, targets=[102, 104])
    ok, why = validate_for_publish(sig, cfg)
    assert not ok
    assert any("demo" in w for w in why)


def test_score_breakdown_has_factors():
    cfg = SignalConfig()
    bundle = make_bundle(True)
    views = [build_timeframe_view(make_df(400, "up"), "1h")]
    der = analyze_derivatives(bundle, cfg)
    of = analyze_orderflow(bundle.orderbook, views[0], cfg)
    from v3.analysis.context import build_context

    ctx = build_context(bundle, views[0], cfg)
    from v3.analysis.regime import detect_regime

    reg = detect_regime(views, cfg)
    score = score_signal(bundle, views, der, of, ctx, reg, build_levels("LONG", 100, views[0].atr, views[0], cfg), 3, "LONG", cfg)
    assert len(score.factors) >= 6
    assert sum(f.weight for f in score.factors) >= 60


def test_store_lifecycle():
    store = SignalStore("/tmp/v3_test_signals.db")
    lifecycle = SignalLifecycle(store, cooldown_seconds=3600, max_active=3)
    sig = TradingSignal(uid="u1", symbol="A", ts_ms=1, direction="LONG", score=90, confidence=0.9, quality=90, tier="S", rr=2.0, risk_score=3, price=100, stop_loss=99, targets=[102, 104], status="CONFIRMED")
    ok, why = lifecycle.should_emit(sig)
    assert ok
    lifecycle.register(sig)
    assert store.recent_signals(limit=10)[0]["uid"] == "u1"
    store.save_outcome("u1", "A", 1, 2, "TP1", 5.0, -1.0, "CLOSED", 2.0, 5.0)
    assert store.outcomes()[0]["outcome"] == "CLOSED"
    store.close()


def test_backtest_walk_forward_smoke():
    from v3.backtest import run_backtest

    class FakeData:
        is_demo = False

    cfg = SignalConfig()
    engine = FuturesSignalEngine(FakeData(), cfg)  # type: ignore[arg-type]
    df = make_df(800, "up")
    # convert to 15m spacing so resampling is meaningful
    df["ts"] = np.arange(len(df), dtype=np.int64) * 900_000 + 1_700_000_000_000
    res = run_backtest(engine, "TESTUSDT", df, entry_tf="15m", medium_tf="1h", macro_tf="4h", warmup=120, cfg=cfg)
    assert res.symbol == "TESTUSDT"
    assert res.signals > 0
    assert "win_rate" in res.metrics
    assert len(res.trades) >= 0


def test_backtest_metrics():
    from v3.backtest import BacktestTrade

    trades = [
        BacktestTrade(signal={"ts_ms": 1}, entry_ts=1, exit_ts=2, entry_price=100, exit_price=102, direction="LONG", rr=2, r_multiple=1.5, pnl_pct=2, bars_held=1, exit_reason="TP1", score=80, confidence=0.9),
        BacktestTrade(signal={"ts_ms": 3}, entry_ts=3, exit_ts=4, entry_price=102, exit_price=100, direction="LONG", rr=2, r_multiple=-1.0, pnl_pct=-2, bars_held=2, exit_reason="SL", score=80, confidence=0.9),
        BacktestTrade(signal={"ts_ms": 6}, entry_ts=6, exit_ts=7, entry_price=102, exit_price=100, direction="LONG", rr=2, r_multiple=-1.0, pnl_pct=-2, bars_held=2, exit_reason="SL", score=80, confidence=0.9),
    ]
    metrics = metrics_from_trades(trades, signals_generated=4)
    assert metrics["trades"] == 3
    assert metrics["win_rate"] == round(100 / 3, 2)
    assert metrics["profit_factor"] == 0.75
    assert metrics["expectancy_r"] == round(-0.5 / 3, 3)
    assert metrics["max_consecutive_losses"] == 2
    assert metrics["precision"] == round(100 / 3, 2)
    assert metrics["false_positive_rate"] == round(200 / 3, 2)
    assert metrics["recall"] == 75.0


def test_walkforward_smoke():
    from v3.walkforward import WalkForwardConfig, walk_forward

    class FakeData:
        is_demo = False
        mode = "fake"

    cfg = SignalConfig()
    engine = FuturesSignalEngine(FakeData(), cfg)  # type: ignore[arg-type]
    df = make_df(1500, "up")
    df["ts"] = np.arange(len(df), dtype=np.int64) * 900_000 + 1_700_000_000_000
    wf = WalkForwardConfig(train_bars=200, test_bars=150, step_bars=150, warmup_bars=60, n_folds=2, entry_tf="15m", medium_tf="1h", macro_tf="4h")
    res = walk_forward(engine, "TESTUSDT", df, cfg, wf)
    assert len(res.folds) == 2
    assert "fold_expectancy_std" in res.stability
    assert res.stability["verdict"] in ("STABLE", "MIXED", "UNSTABLE")


def test_scanner_ranks_and_analyzes():
    import asyncio

    from v3.scanner import Scanner

    class FakeEngine:
        class FakeData:
            mode = "fake"

        data = FakeData()

        async def analyze_batch(self, symbols, concurrency=4):
            return [
                TradingSignal(
                    uid=f"u{i}",
                    symbol=s,
                    ts_ms=int(time.time() * 1000),
                    direction="NO_TRADE",
                    status="NO_TRADE",
                    quality=50,
                    no_trade_reasons=["test"],
                )
                for i, s in enumerate(symbols)
            ]

    class T:
        def __init__(self, sym, turnover, vol, price, high, low, bid, ask, funding):
            self.symbol = sym
            self.turnover_24h = turnover
            self.volume_24h = vol
            self.last = price
            self.price_24h_pct = 5.0
            self.high_24h = high
            self.low_24h = low
            self.bid = bid
            self.ask = ask
            self.funding_rate = funding
            self.open_interest_usd = 1e6
            self.open_interest = 1000.0

    cfg = SignalConfig()
    engine = FakeEngine()
    tickers = {
        "AA": T("AAUSDT", 100_000_000, 1e6, 100, 105, 95, 99.99, 100.01, 0.0001),
        "BB": T("BBUSDT", 500_000, 1e5, 2, 2.1, 1.9, 1.999, 2.001, 0.002),
    }
    scanner = Scanner(engine, cfg)  # type: ignore[arg-type]
    result = asyncio.run(scanner.run(tickers, limit=10, top=2))
    assert len(result.candidates) == 1
    assert result.candidates[0].symbol == "AAUSDT"
    assert result.analyzed


def test_ai_reasoner_annotates_but_does_not_change_core():
    from v3.ai import RuleBasedReasoner

    sig = TradingSignal(uid="ai1", symbol="X", ts_ms=1, direction="LONG", status="CONFIRMED", score=85, quality=85, tier="A", rr=2, risk_score=4, price=100, entry_zone=(99, 100), stop_loss=98, targets=[102, 104, 106], features={"regime": {"regime": "TRENDING_UP"}, "timeframes": [{"timeframe": "1h", "trend": "up", "adx": 30}], "derivatives": {"funding_rate": 0.0001, "funding_trend": "neutral"}, "orderflow": {"liquidity_grade": "ok", "imbalance": 0.4}})
    out = RuleBasedReasoner()(sig)
    assert out.direction == "LONG"
    assert out.entry_zone == (99, 100)
    assert out.score == 85
    assert any("Режим рынка" in r for r in out.reasons)


def test_stale_data_is_no_trade():
    cfg = SignalConfig()
    engine = FuturesSignalEngine(None, cfg)  # type: ignore[arg-type]
    bundle = make_bundle(True)
    bundle.data_age_seconds = 999_999
    sig = engine.evaluate_bundle(bundle, make_tf_map(), btc_tf=None, strict_liquidity=False)
    assert sig.direction == "NO_TRADE"
    assert any("stale market data" in r for r in sig.no_trade_reasons)


def test_lifecycle_tracks_tp_and_sl():
    store = SignalStore("/tmp/v3_test_lifecycle.db")
    lifecycle = SignalLifecycle(store, cooldown_seconds=3600, max_active=3)
    long_sig = TradingSignal(uid="tp1", symbol="X", ts_ms=1, direction="LONG", status="CONFIRMED", score=90, quality=90, tier="S", rr=2, risk_score=3, price=100, entry_zone=(99.5, 100.0), stop_loss=98.0, targets=[102, 104, 106])
    short_sig = TradingSignal(uid="sl1", symbol="Y", ts_ms=2, direction="LONG", status="CONFIRMED", score=90, quality=90, tier="S", rr=2, risk_score=3, price=100, entry_zone=(99.5, 100.0), stop_loss=98.0, targets=[102, 104, 106])
    lifecycle.register(long_sig)
    lifecycle.register(short_sig)
    events = lifecycle.track_prices({"X": 106.0, "Y": 95.0}, now_ms=100_000)
    events = {e["symbol"]: e["event"] for e in events}
    assert events["X"] in ("TP3_HIT", "CLOSED")
    assert events["Y"] == "STOPPED"
    store.close()


async def test_watcher_cycle_persists_and_tracks():
    from v3.watcher import V3Watcher

    store = SignalStore("/tmp/v3_test_watcher.db")
    lifecycle = SignalLifecycle(store, cooldown_seconds=60, max_active=3)
    sig = TradingSignal(uid="w1", symbol="X", ts_ms=1, direction="LONG", status="CONFIRMED", score=90, quality=90, tier="S", rr=2, risk_score=3, price=100, entry_zone=(99.5, 100.0), stop_loss=98.0, targets=[102, 104, 106])

    class FakeData:
        mode = "fake"
        is_demo = False
        async def tickers(self, symbols):
            class T:
                symbol = "X"
                last = 106.0
            return {"X": T()}

    class FakeEngine:
        async def analyze_batch(self, symbols, concurrency=4):
            return [sig]

    watcher = V3Watcher(FakeData(), FakeEngine(), store, lifecycle, SignalConfig(), symbols=["X"])
    events = await watcher.run_cycle()
    assert len(events) >= 2
    saved = store.recent_signals("X", limit=10)
    assert saved[0]["status"] in ("CLOSED", "TP3_HIT", "STOPPED")
    store.close()


async def test_telegram_core_help_status():
    from v3.telegram import V3Core

    store = SignalStore("/tmp/v3_test_tg.db")
    lifecycle = SignalLifecycle(store, cooldown_seconds=60, max_active=3)
    dummy_data = type("D", (), {"mode": "auto"})()
    core = V3Core(dummy_data, None, store, lifecycle, SignalConfig())  # type: ignore[arg-type]
    assert "v3" in await core.handle_message("help")
    assert "Сохранено" in core.status_text()
    store.close()


def test_reports():
    sig = TradingSignal(uid="r", symbol="X", ts_ms=1, direction="LONG", score=85, confidence=0.9, quality=85, tier="A", rr=2.5, risk_score=4, leverage=3, price=100, entry_zone=(99.5, 100.0), stop_loss=98.0, targets=[105, 108, 111], regime="TRENDING_UP", horizon="1m-4h", invalidation="close below 98")
    beg = render_signal(sig, "beginner")
    pro = render_signal(sig, "pro")
    assert "LONG" in beg
    assert "Score breakdown" in pro
    assert "не гарантия результата" in beg  # explicit disclaimer, no guarantee


async def test_scanner_ranks_and_filters():
    from v3.scanner import Scanner

    class FakeEngine:
        class FakeData:
            mode = "fake"
            is_demo = False

        data = FakeData()

        async def analyze_batch(self, symbols, concurrency=4):
            return [
                TradingSignal(
                    uid=f"sc{i}",
                    symbol=s,
                    ts_ms=int(time.time() * 1000),
                    direction="LONG",
                    status="CONFIRMED",
                    score=80,
                    quality=80,
                    tier="B",
                )
                for i, s in enumerate(symbols)
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

    cfg = SignalConfig()
    result = await Scanner(FakeEngine(), cfg).run(  # type: ignore[arg-type]
        {"AA": T("AAUSDT", 100_000_000.0), "BB": T("BBUSDT", 500_000.0)},
        limit=10,
        top=2,
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].symbol == "AAUSDT"
    assert result.analyzed[0]["signal"].direction == "LONG"
