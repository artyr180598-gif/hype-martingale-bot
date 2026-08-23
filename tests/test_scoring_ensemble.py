"""
Tests for Signal Scoring, Ensemble, and Risk Management.
"""
from src.config.constants import RiskProfile
from src.features.pipeline import FeaturePipeline
from src.risk.position_sizer import PositionSizer
from src.risk.risk_manager import RiskManager
from src.signals.generator import SignalGenerator


def test_signal_generator_and_scoring(sample_candles, sample_orderbook):
    pipeline = FeaturePipeline()
    feat = pipeline.compute_feature_matrix(sample_candles, orderbook=sample_orderbook)

    setup = SignalGenerator.generate_setup(entry_features=feat)
    assert setup.symbol == "BTCUSDT"
    assert setup.score >= 0.0
    assert setup.score_breakdown.total_score >= 0.0
    assert setup.recommended_leverage >= 1


def test_position_sizer():
    sizing = PositionSizer.calculate_sizing(
        account_equity=10000.0,
        entry_price=50000.0,
        stop_loss=49000.0,  # 2% stop distance
        direction="LONG",
        risk_profile=RiskProfile.BALANCED,
    )
    assert sizing.risk_usd == 150.0  # 1.5% of $10,000
    assert sizing.quantity == 0.15   # $150 / $1000 stop distance
    assert sizing.notional_value_usd == 7500.0
    assert sizing.margin_required_usd > 0
    assert sizing.estimated_liquidation_price < 49000.0  # Safe buffer


def test_risk_manager_approval():
    plan = RiskManager.evaluate_trade_risk(
        symbol="BTCUSDT",
        direction="LONG",
        entry_price=50000.0,
        stop_loss=49000.0,
        account_equity=10000.0,
        open_positions_count=2,
    )
    assert plan.is_approved_by_risk_guard is True
