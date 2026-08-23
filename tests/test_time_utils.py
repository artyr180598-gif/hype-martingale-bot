"""
Tests for UTC time utilities.
"""
from datetime import datetime, timezone

from src.core.time_utils import (
    align_timestamp_to_bar,
    calculate_bar_range,
    timeframe_to_ms,
    to_utc_datetime,
    to_utc_ms,
    utc_now,
)


def test_utc_now_is_timezone_aware():
    now = utc_now()
    assert now.tzinfo == timezone.utc
    assert isinstance(now, datetime)


def test_to_utc_datetime_from_ms():
    ts_ms = 1700000000000
    dt = to_utc_datetime(ts_ms)
    assert dt.tzinfo == timezone.utc
    assert to_utc_ms(dt) == ts_ms


def test_timeframe_to_ms():
    assert timeframe_to_ms("1m") == 60_000
    assert timeframe_to_ms("5m") == 300_000
    assert timeframe_to_ms("15m") == 900_000
    assert timeframe_to_ms("1h") == 3_600_000
    assert timeframe_to_ms("4h") == 14_400_000
    assert timeframe_to_ms("1d") == 86_400_000


def test_align_timestamp_to_bar():
    # 15m = 900,000 ms
    ts = 1700000123456
    aligned = align_timestamp_to_bar(ts, "15m")
    assert aligned % 900_000 == 0
    assert aligned <= ts


def test_calculate_bar_range():
    start = 1700000000000
    end = start + (10 * 900_000)
    norm_start, norm_end, expected_bars = calculate_bar_range(start, end, "15m")
    assert expected_bars == 11
