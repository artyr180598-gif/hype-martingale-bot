"""
Tests for Risk Engine, Position Sizing, Dynamic Leverage, and Stop Loss.
"""
from src.config.constants import RiskProfile
from src.risk.leverage import LeverageEngine
from src.risk.position_sizer import PositionSizer
from src.risk.risk_manager import RiskManager
from src.risk.stop_loss import StopLossEngine, TakeProfitEngine


def test_position_sizing_respects_max_risk() -> None:
    capital = 10000.0
    entry = 100.0
    stop = 95.0  # 5% distance
    profile = RiskProfile.BALANCED  # 1.5% risk = $150 risk amount

    sizing = PositionSizer.calculate_sizing(
        account_equity=capital,
        entry_price=entry,
        stop_loss=stop,
        direction="LONG",
        risk_profile=profile,
    )

    assert sizing.risk_usd == 150.0
    assert sizing.quantity == 30.0  # 150 / 5 = 30 units
    assert sizing.notional_value_usd == 3000.0
    assert sizing.risk_percentage == 1.5


def test_dynamic_leverage_recommendation() -> None:
    # Tight stop -> higher leverage recommendation
    rec_tight = LeverageEngine.recommend_leverage(
        stop_loss_distance_pct=1.0,
        volatility_regime="NORMAL",
        max_leverage_ceiling=10,
    )
    assert 2 <= rec_tight.recommended_leverage <= 10
    assert rec_tight.liquidation_distance_pct > 0

    # Extreme volatility -> capped leverage
    rec_high_vol = LeverageEngine.recommend_leverage(
        stop_loss_distance_pct=1.0,
        volatility_regime="EXTREME_VOLATILITY",
        max_leverage_ceiling=10,
    )
    assert rec_high_vol.recommended_leverage <= 5


def test_stop_loss_and_take_profit_engines() -> None:
    sl_long = StopLossEngine.calculate_atr_stop(
        entry_price=100.0,
        atr=2.0,
        direction="LONG",
        atr_multiplier=1.5,
    )
    assert sl_long == 97.0

    sl_short = StopLossEngine.calculate_atr_stop(
        entry_price=100.0,
        atr=2.0,
        direction="SHORT",
        atr_multiplier=1.5,
    )
    assert sl_short == 103.0

    tp1, tp2, tp3 = TakeProfitEngine.calculate_targets(
        entry_price=100.0,
        stop_loss=95.0,
        direction="LONG",
    )
    assert tp1 == 107.5
    assert tp2 == 112.5
    assert tp3 == 120.0


def test_risk_manager_guard() -> None:
    # Valid plan
    plan_valid = RiskManager.evaluate_trade_risk(
        symbol="BTCUSDT",
        direction="LONG",
        entry_price=50000.0,
        stop_loss=49000.0,
        account_equity=10000.0,
        open_positions_count=1,
        current_portfolio_risk_pct=1.5,
    )
    assert plan_valid.is_approved_by_risk_guard is True

    # Position count cap exceeded
    plan_rejected = RiskManager.evaluate_trade_risk(
        symbol="ETHUSDT",
        direction="LONG",
        entry_price=3000.0,
        stop_loss=2900.0,
        account_equity=10000.0,
        open_positions_count=10,  # exceeds max 4
        current_portfolio_risk_pct=1.5,
    )
    assert plan_rejected.is_approved_by_risk_guard is False
    assert any("positions limit" in r for r in plan_rejected.rejection_reasons)
