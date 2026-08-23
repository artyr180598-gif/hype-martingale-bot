"""
Tests for Strategy Registry and Strategy Implementations.
"""
from src.config.constants import SignalDirection
from src.strategies.breakout import BreakoutStrategy
from src.strategies.funding_squeeze import FundingSqueezeStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.registry import StrategyRegistry
from src.strategies.trend_following import TrendFollowingStrategy


def test_strategy_registry_initialization() -> None:
    all_strats = StrategyRegistry.list_all()
    active_strats = StrategyRegistry.list_active()
    assert len(all_strats) >= 6
    assert len(active_strats) >= 6

    trend = StrategyRegistry.get("TrendFollowingStrategy")
    assert trend is not None
    assert trend.name == "TrendFollowingStrategy"


def test_trend_following_evaluation() -> None:
    strat = TrendFollowingStrategy()

    # Bullish market features matching conditions: close > ema50 > ema200, ema9 > ema21, structure="BULLISH", adx >= 20
    features = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "close": 52000.0,
        "ema_9": 51800.0,
        "ema_21": 51500.0,
        "ema_50": 51000.0,
        "ema_200": 49000.0,
        "adx_14": 30.0,
        "rsi_14": 58.0,
        "structure_state": "BULLISH",
        "atr_14": 500.0,
        "last_swing_high": 52500.0,
        "last_swing_low": 50500.0,
    }

    sig = strat.evaluate(features)
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.take_profit_1 > sig.entry_price
    assert sig.stop_loss < sig.entry_price


def test_funding_squeeze_strategy() -> None:
    strat = FundingSqueezeStrategy()

    # Negative funding + high OI + bullish sweep = short squeeze long setup
    features = {
        "symbol": "ETHUSDT",
        "timeframe": "15m",
        "close": 3000.0,
        "funding_rate": -0.0006,  # -0.06%
        "funding_zscore": -2.5,
        "oi_zscore": 2.1,
        "cvd_slope_10": 1.5,
        "atr_14": 40.0,
    }

    sig = strat.evaluate(features)
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.confidence >= 0.70


def test_mean_reversion_strategy() -> None:
    strat = MeanReversionStrategy()

    # Extreme overbought condition with vwap_dist_pct > 2.2 and rsi > 68 and adx < 28
    features = {
        "symbol": "SOLUSDT",
        "timeframe": "15m",
        "close": 155.0,
        "vwap": 150.0,
        "vwap_dist_pct": 3.33,
        "rsi_14": 78.0,
        "stoch_k": 85.0,
        "adx_14": 18.0,
        "structure_state": "RANGE",
        "atr_14": 3.0,
    }

    sig = strat.evaluate(features)
    assert sig is not None
    assert sig.direction == SignalDirection.SHORT
    assert sig.take_profit_1 < sig.entry_price


def test_breakout_strategy() -> None:
    strat = BreakoutStrategy()

    features = {
        "symbol": "BNBUSDT",
        "timeframe": "15m",
        "close": 605.0,
        "last_swing_high": 600.0,
        "last_swing_low": 580.0,
        "bos_bullish": True,
        "is_squeeze": True,
        "bb_width_percentile": 20.0,
        "atr_14": 6.0,
    }

    sig = strat.evaluate(features)
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.risk_reward_ratio >= 1.5
