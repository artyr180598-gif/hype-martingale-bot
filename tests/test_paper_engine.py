"""
Tests for Paper Trading Simulator and Virtual Portfolio.
"""
from src.config.constants import EntryType, SignalDirection, SignalTier
from src.paper.engine import PaperTradingEngine
from src.signals.models import ScenarioProbabilities, ScoreBreakdown, SignalSetup


def test_paper_engine_lifecycle() -> None:
    engine = PaperTradingEngine(initial_balance=10000.0)

    breakdown = ScoreBreakdown(
        trend=15.0,
        market_structure=15.0,
        order_flow=15.0,
        volatility=10.0,
        open_interest=10.0,
        volume=10.0,
        momentum=10.0,
    )

    scenarios = ScenarioProbabilities(
        long_probability_pct=75.0,
        short_probability_pct=15.0,
        no_trade_probability_pct=10.0,
    )

    signal = SignalSetup(
        signal_id="SIG-TEST-001",
        symbol="BTCUSDT",
        timeframe="15m",
        timestamp_ms=1700000000000,
        direction=SignalDirection.LONG,
        tier=SignalTier.STRONG,
        score=89.0,
        confidence=0.89,
        entry_type=EntryType.MARKET,
        entry_price=50000.0,
        entry_zone="$49900 – $50100",
        stop_loss=49000.0,
        take_profit_1=52000.0,
        take_profit_2=54000.0,
        take_profit_3=56000.0,
        risk_reward_ratio=2.0,
        recommended_leverage=5,
        invalidation_condition="Close below $49,000",
        primary_reasons=["Bullish trend continuation"],
        risk_factors=[],
        score_breakdown=breakdown,
        scenario_probabilities=scenarios,
        market_regime="BULL_STRONG_TREND",
        strategy_source="TrendFollowingStrategy",
    )

    # 1. Open paper position with $1,000 allocated margin
    pos = engine.open_position_from_signal(signal, allocated_margin=1000.0)
    assert pos is not None
    assert pos.symbol == "BTCUSDT"
    assert pos.side == "LONG"
    assert pos.leverage == 5
    assert pos.quantity == 0.1  # ($1000 * 5) / $50000 = 0.1 BTC
    assert "BTCUSDT" in engine.portfolio.open_positions

    # 2. Update price upward -> check unrealized PnL
    events = engine.update_price_and_check_triggers("BTCUSDT", 51000.0)
    assert len(events) == 0
    assert engine.portfolio.open_positions["BTCUSDT"].unrealized_pnl == 100.0  # 0.1 * 1000 = $100

    # 3. Update price to TP1 (52,000) -> should trigger TP close
    close_events = engine.update_price_and_check_triggers("BTCUSDT", 52000.0)
    assert len(close_events) == 1
    assert close_events[0]["reason"] == "TAKE_PROFIT_1"
    assert close_events[0]["net_pnl"] > 190.0  # $200 gross - fees
    assert "BTCUSDT" not in engine.portfolio.open_positions
    assert engine.portfolio.closed_trades_count == 1
    assert engine.portfolio.winning_trades_count == 1
