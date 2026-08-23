"""
Tests for Alert Dispatcher and Scanner Logic.
"""
from src.alerts.dispatcher import AlertDispatcher
from src.config.constants import EntryType, SignalDirection, SignalTier
from src.signals.models import ScenarioProbabilities, ScoreBreakdown, SignalSetup


def _create_mock_setup(symbol: str, score: float, direction: SignalDirection) -> SignalSetup:
    breakdown = ScoreBreakdown(
        trend=score / 6,
        market_structure=score / 6,
        order_flow=score / 6,
        volatility=score / 6,
        open_interest=score / 12,
        volume=score / 12,
        momentum=score / 6,
    )
    scenarios = ScenarioProbabilities(
        long_probability_pct=70.0 if direction == SignalDirection.LONG else 15.0,
        short_probability_pct=70.0 if direction == SignalDirection.SHORT else 15.0,
        no_trade_probability_pct=15.0 if direction != SignalDirection.NO_TRADE else 70.0,
    )
    return SignalSetup(
        signal_id=f"SIG-{symbol}-123",
        symbol=symbol,
        timeframe="15m",
        timestamp_ms=1700000000000,
        direction=direction,
        tier=SignalTier.STRONG if score >= 75 else SignalTier.VALID,
        score=score,
        confidence=score / 100.0,
        entry_type=EntryType.MARKET,
        entry_price=100.0,
        entry_zone="$99 – $101",
        stop_loss=95.0,
        take_profit_1=110.0,
        risk_reward_ratio=2.0,
        recommended_leverage=5,
        invalidation_condition="SL hit",
        primary_reasons=["Test reason"],
        risk_factors=[],
        score_breakdown=breakdown,
        scenario_probabilities=scenarios,
        market_regime="BULL_STRONG_TREND",
        strategy_source="TrendFollowingStrategy",
    )


def test_alert_dispatcher_deduplication() -> None:
    dispatcher = AlertDispatcher(cooldown_seconds=3600.0)
    setup = _create_mock_setup("ETHUSDT", 82.0, SignalDirection.LONG)

    # First dispatch should be accepted
    should_send_1 = dispatcher.should_dispatch_alert(setup)
    assert should_send_1 is True

    # Immediate second dispatch for the exact same event hash should be throttled/deduplicated
    should_send_2 = dispatcher.should_dispatch_alert(setup)
    assert should_send_2 is False


def test_alert_dispatcher_low_score_suppression() -> None:
    dispatcher = AlertDispatcher()
    setup_low = _create_mock_setup("BTCUSDT", 60.0, SignalDirection.LONG)  # Below 75 threshold
    assert dispatcher.should_dispatch_alert(setup_low) is False
