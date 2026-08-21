from datetime import timedelta
from decimal import Decimal

import pytest

from app.data.candles import normalize_bybit_kline


def test_normalizes_bybit_kline() -> None:
    candle = normalize_bybit_kline(
        [1710000000000, "70000", "70500", "69500", "70200", "100", "7000000"],
        "BTCUSDT", "1",
    )
    assert candle.symbol == "BTCUSDT"
    assert candle.close == Decimal("70200")
    assert candle.quote_volume == Decimal("7000000")
    assert candle.close_time - candle.open_time == timedelta(minutes=1)


def test_rejects_invalid_ohlc() -> None:
    with pytest.raises(ValueError, match="invalid_ohlc"):
        normalize_bybit_kline(
            [1710000000000, "70000", "69000", "69500", "70200", "100", "7000000"],
            "BTCUSDT", "1",
        )
