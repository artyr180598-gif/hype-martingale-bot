"""
Tests for Telegram Message Formatters and Score Bar Renderers.
"""
from src.bot.formatters import BotFormatters, render_progress_bar
from src.config.constants import EntryType, SignalDirection, SignalTier
from src.core.time_utils import utc_now_ms
from src.signals.models import ScenarioProbabilities, ScoreBreakdown, SignalSetup


def test_render_progress_bar():
    bar_full = render_progress_bar(15, 15, length=10)
    assert bar_full == "██████████"

    bar_half = render_progress_bar(5, 10, length=10)
    assert bar_half == "█████░░░░░"

    bar_empty = render_progress_bar(0, 10, length=10)
    assert bar_empty == "░░░░░░░░░░"


def test_format_signal_card():
    setup = SignalSetup(
        signal_id="SIG-BTC-001",
        symbol="BTCUSDT",
        timeframe="15m",
        timestamp_ms=utc_now_ms(),
        direction=SignalDirection.LONG,
        tier=SignalTier.STRONG,
        score=84.5,
        confidence=0.85,
        entry_type=EntryType.MARKET,
        entry_price=64250.0,
        entry_zone="$64,200 – $64,300",
        stop_loss=63100.0,
        take_profit_1=66000.0,
        take_profit_2=67500.0,
        take_profit_3=69000.0,
        risk_reward_ratio=2.8,
        recommended_leverage=5,
        invalidation_condition="4H close below $63,100",
        primary_reasons=["4H trend bullish", "15m BOS confirmed", "OI +8.2% expansion"],
        risk_factors=["High volatility"],
        score_breakdown=ScoreBreakdown(trend=13, market_structure=14, order_flow=12, volatility=8, open_interest=8),
        scenario_probabilities=ScenarioProbabilities(long_probability_pct=72, short_probability_pct=15, no_trade_probability_pct=13),
        market_regime="STRONG_UPTREND",
    )
    formatted = BotFormatters.format_signal(setup)
    assert "BTCUSDT" in formatted
    assert "84/100" in formatted or "85/100" in formatted
    assert "Risk/Reward" in formatted
