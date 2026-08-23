"""
Tests for Data Quality Engine.
"""
from src.core.time_utils import utc_now_ms
from src.data.models import CandleData, DataQualityStatus
from src.data.quality import DataQualityEngine


def test_data_quality_valid(sample_candles):
    report = DataQualityEngine.validate_candles(sample_candles, "15m")
    assert report.total_candles == len(sample_candles)
    assert report.status in (DataQualityStatus.EXCELLENT, DataQualityStatus.GOOD)
    assert report.is_acceptable_for_trading is True
    assert report.quality_score >= 0.80


def test_data_quality_empty():
    report = DataQualityEngine.validate_candles([], "15m")
    assert report.status == DataQualityStatus.INVALID
    assert report.is_acceptable_for_trading is False
    assert report.quality_score == 0.0


def test_data_quality_bad_prices():
    bad_candle = CandleData(
        symbol="BTCUSDT",
        timeframe="15m",
        timestamp_ms=utc_now_ms(),
        open=50000.0,
        high=40000.0,  # Invalid: high < low
        low=45000.0,
        close=48000.0,
        volume=-10.0,   # Invalid: negative volume
    )
    report = DataQualityEngine.validate_candles([bad_candle], "15m")
    assert report.invalid_price_count > 0
    assert report.is_acceptable_for_trading is False
