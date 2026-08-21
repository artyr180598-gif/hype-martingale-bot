from app.core.config import Settings


def test_live_trading_is_disabled_by_default() -> None:
    settings = Settings()
    assert settings.enable_live_trading is False


def test_risk_limit_is_bounded() -> None:
    settings = Settings(max_risk_per_trade=0.01)
    assert 0 < settings.max_risk_per_trade <= 0.10
