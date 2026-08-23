"""
Tests for No-Trade Suppression and Conflict Resolution.
"""
from src.config.constants import EntryType, SignalDirection, SignalTier
from src.core.time_utils import utc_now_ms
from src.signals.conflict_resolution import ConflictResolver
from src.signals.models import ScenarioProbabilities, ScoreBreakdown, SignalSetup
from src.signals.no_trade import NoTradeEngine
from src.strategies.base import StrategySignal


def test_conflict_resolution_detection():
    # Long and Short signals competing
    sig_long = StrategySignal(
        strategy_name="TrendFollowingStrategy",
        strategy_version="1.0",
        symbol="BTCUSDT",
        timeframe="15m",
        timestamp_ms=utc_now_ms(),
        direction=SignalDirection.LONG,
        score=85.0,
        confidence=0.85,
        entry_type=EntryType.MARKET,
        entry_price=50000.0,
        stop_loss=49000.0,
        take_profit_1=52000.0,
        take_profit_2=54000.0,
        take_profit_3=56000.0,
        risk_reward_ratio=2.0,
        invalidation="SL",
        reasons=[],
        risk_warnings=[],
    )
    sig_short = StrategySignal(
        strategy_name="MeanReversionStrategy",
        strategy_version="1.0",
        symbol="BTCUSDT",
        timeframe="15m",
        timestamp_ms=utc_now_ms(),
        direction=SignalDirection.SHORT,
        score=82.0,
        confidence=0.82,
        entry_type=EntryType.MARKET,
        entry_price=50000.0,
        stop_loss=51000.0,
        take_profit_1=48000.0,
        take_profit_2=46000.0,
        take_profit_3=44000.0,
        risk_reward_ratio=2.0,
        invalidation="SL",
        reasons=[],
        risk_warnings=[],
    )

    has_conflict, probs, reasons = ConflictResolver.resolve_conflicts([sig_long, sig_short])
    assert has_conflict is True
    assert probs.no_trade_probability_pct >= 20.0


def test_no_trade_suppression_low_score():
    setup = SignalSetup(
        signal_id="SIG-TEST",
        symbol="BTCUSDT",
        timeframe="15m",
        timestamp_ms=utc_now_ms(),
        direction=SignalDirection.LONG,
        tier=SignalTier.WATCH,
        score=55.0,  # Below 60 threshold
        confidence=0.55,
        entry_type=EntryType.MARKET,
        entry_price=50000.0,
        entry_zone="$49,900 - $50,100",
        stop_loss=49000.0,
        take_profit_1=52000.0,
        risk_reward_ratio=2.0,
        invalidation_condition="SL",
        score_breakdown=ScoreBreakdown(),
        scenario_probabilities=ScenarioProbabilities(long_probability_pct=50, short_probability_pct=20, no_trade_probability_pct=30),
        market_regime="RANGE",
    )
    suppress, reasons = NoTradeEngine.should_suppress_signal(setup)
    assert suppress is True
    assert any("threshold" in r for r in reasons)
