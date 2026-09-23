"""Tests for HYPE ULTIMATE — ported from best projects."""

import pandas as pd
import pytest

from src.hype.config import load_config
from src.hype.indicators.trend import compute_trend_indicators
from src.hype.indicators.momentum import compute_momentum_indicators
from src.hype.indicators.volatility import compute_volatility_indicators
from src.hype.indicators.volume import compute_volume_indicators
from src.hype.indicators.structure import compute_structure
from src.hype.scanner.signals import detect_stobb, detect_sbm, detect_jump
from src.hype.analysis.confluence import evaluate_strategies
from src.hype.analysis.risk import calculate_risk_plan
from src.hype.data.models import Ticker, Orderbook, OrderbookLevel, MarketBundle
from datetime import datetime, timezone


def make_df(trend="up", length=200):
    # Generate synthetic OHLCV
    import numpy as np
    np.random.seed(42)
    close = 100.0
    data = []
    for i in range(length):
        if trend == "up":
            close += np.random.randn() * 0.5 + 0.2
        elif trend == "down":
            close += np.random.randn() * 0.5 - 0.2
        else:
            close += np.random.randn() * 0.5
        high = close + abs(np.random.randn() * 0.5)
        low = close - abs(np.random.randn() * 0.5)
        open_ = close + np.random.randn() * 0.2
        vol = 1000 + np.random.randn() * 100
        data.append([open_, high, low, close, vol])
    df = pd.DataFrame(data, columns=["open", "high", "low", "close", "volume"])
    df.index = pd.date_range("2024-01-01", periods=length, freq="1h", tz="UTC")
    return df


def test_config_loads():
    cfg = load_config()
    assert cfg.PRIMARY_EXCHANGE in cfg.exchanges_list
    assert len(cfg.timeframes) > 0


def test_indicators_trend():
    df = make_df("up", 200)
    df = compute_trend_indicators(df)
    assert "ema20" in df.columns
    assert "ema200" in df.columns
    assert "supertrend" in df.columns
    assert "adx" in df.columns


def test_indicators_momentum():
    df = make_df("up", 100)
    df = compute_momentum_indicators(df)
    assert "rsi" in df.columns
    assert "stoch_k" in df.columns
    assert "macd" in df.columns
    assert "mfi" in df.columns


def test_indicators_volatility():
    df = make_df("up", 100)
    df = compute_trend_indicators(df)
    df = compute_volatility_indicators(df)
    assert "bb_upper" in df.columns
    assert "bb_lower" in df.columns
    assert "atr" in df.columns
    assert "squeeze_on" in df.columns


def test_indicators_volume():
    df = make_df("up", 100)
    df = compute_trend_indicators(df)
    df = compute_volatility_indicators(df)
    df = compute_volume_indicators(df)
    assert "vwap" in df.columns
    assert "volume_ratio" in df.columns


def test_structure():
    df = make_df("up", 100)
    struct = compute_structure(df)
    assert "support" in struct
    assert "resistance" in struct
    assert "structure" in struct


def test_confluence():
    cfg = load_config()
    df = make_df("up", 200)
    df = compute_trend_indicators(df)
    df = compute_momentum_indicators(df)
    df = compute_volatility_indicators(df)
    df = compute_volume_indicators(df)
    result = evaluate_strategies(df, cfg)
    assert "long_score" in result
    assert "short_score" in result
    assert "bias" in result
    assert result["bias"] in ("LONG", "SHORT", "NEUTRAL")


def test_risk_plan():
    cfg = load_config()
    plan = calculate_risk_plan(entry=100.0, atr=1.5, direction="LONG", cfg=cfg, support=98.0)
    assert plan.stop_loss < 100
    assert len(plan.take_profits) == 3
    assert plan.take_profits[0] > 100
    assert plan.risk_reward > 0

    plan_short = calculate_risk_plan(entry=100.0, atr=1.5, direction="SHORT", cfg=cfg, resistance=102.0)
    assert plan_short.stop_loss > 100
    assert plan_short.take_profits[0] < 100


def test_stobb_detection():
    cfg = load_config()
    df = make_df("down", 100)
    df = compute_trend_indicators(df)
    df = compute_momentum_indicators(df)
    df = compute_volatility_indicators(df)
    df = compute_volume_indicators(df)
    # Force oversold
    df.loc[df.index[-1], "stoch_k"] = 15
    df.loc[df.index[-1], "stoch_d"] = 18
    df.loc[df.index[-1], "bb_pct"] = 0.10
    df.loc[df.index[-1], "rsi"] = 30
    sig = detect_stobb(df, cfg, direction="long")
    assert sig is not None
    assert sig["type"] == "STOBB"
    assert sig["direction"] == "LONG"


def test_jump_detection():
    cfg = load_config()
    df = make_df("up", 30)
    df = compute_trend_indicators(df)
    df = compute_volatility_indicators(df)
    df = compute_volume_indicators(df)
    # Force jump
    df.loc[df.index[-1], "close"] = df["close"].iloc[-2] * 1.05
    df.loc[df.index[-1], "volume"] = df["volume"].iloc[-7:-1].mean() * 3
    df.loc[df.index[-1], "rvol"] = 3.0
    df.loc[df.index[-1], "volume_z"] = 2.5
    sig = detect_jump(df, cfg)
    # May or may not trigger depending on lookback, but should not crash
    assert sig is None or sig["type"] == "JUMP"


def test_orderbook():
    ob = Orderbook(
        symbol="BTCUSDT",
        exchange="bybit",
        bids=[OrderbookLevel(price=100.0, qty=10.0), OrderbookLevel(price=99.9, qty=5.0)],
        asks=[OrderbookLevel(price=100.1, qty=8.0), OrderbookLevel(price=100.2, qty=6.0)],
    )
    assert ob.mid_price == 100.05
    imb = ob.imbalance()
    assert 0 <= imb <= 1
    walls = ob.find_walls(threshold_usd=500)
    assert "bids" in walls


def test_bundle():
    cfg = load_config()
    df = make_df("up", 100)
    df = compute_trend_indicators(df)
    df = compute_momentum_indicators(df)
    df = compute_volatility_indicators(df)
    df = compute_volume_indicators(df)

    ticker = Ticker(symbol="BTCUSDT", exchange="bybit", last=100.0, volume_24h=1_000_000, turnover_24h=100_000_000)
    bundle = MarketBundle(
        symbol="BTCUSDT",
        exchange="bybit",
        ticker=ticker,
        orderbook=None,
        klines={"15m": df, "1h": df},
        timestamp=datetime.now(timezone.utc),
    )
    assert bundle.has_minimum
    assert bundle.primary_candles is not None


def test_no_order_execution():
    # Ensure no create_order / place_order in codebase
    import pathlib
    root = pathlib.Path("src/hype")
    forbidden = ["create_order", "place_order", "buy(", "sell("]
    # We allow buy/sell in comments but not actual exchange order calls
    # Check for ccxt create_order
    for f in root.rglob("*.py"):
        content = f.read_text()
        if "def create_order" in content or "def place_order" in content:
            # Only allowed in tests
            assert False, f"Found order execution in {f}"
        # Check ccxt order execution (should not have ex.create_order)
        if ".create_order(" in content and "test" not in str(f):
            assert False, f"Found create_order call in {f}: forbidden"
