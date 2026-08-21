from datetime import timedelta
from decimal import Decimal

from app.core.time import utc_now
from app.data.quality import DataQualityEngine


def test_rejects_non_positive_trade() -> None:
    result = DataQualityEngine().validate_trade(Decimal("100"), Decimal("0"))
    assert result.valid is False
    assert "non_positive_quantity" in result.reasons


def test_accepts_valid_trade() -> None:
    result = DataQualityEngine().validate_trade(Decimal("100"), Decimal("0.5"))
    assert result.valid is True


def test_rejects_future_timestamp() -> None:
    received = utc_now()
    result = DataQualityEngine().validate_timestamp(
        received + timedelta(seconds=10), received
    )
    assert result.valid is False
