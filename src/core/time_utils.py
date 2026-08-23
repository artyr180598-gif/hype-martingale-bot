"""
Time and Date Utilities — Strictly UTC compliant.
"""
from datetime import datetime, timedelta, timezone

from src.config.constants import TIMEFRAME_MS


def utc_now() -> datetime:
    """Return the current datetime strictly in UTC timezone."""
    return datetime.now(timezone.utc)


def utc_now_ms() -> int:
    """Return current UTC timestamp in milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def to_utc_datetime(ts: float | str | datetime) -> datetime:
    """Convert integer ms, float seconds, or string to a timezone-aware UTC datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    if isinstance(ts, (int, float)):
        # If timestamp is in milliseconds (> 10^11)
        if ts > 100_000_000_000:
            return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    if isinstance(ts, str):
        # Clean ISO or standard string
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    raise ValueError(f"Unable to parse timestamp to UTC datetime: {ts}")


def to_utc_ms(ts: float | str | datetime) -> int:
    """Convert any supported timestamp representation to UTC timestamp in milliseconds."""
    dt = to_utc_datetime(ts)
    return int(dt.timestamp() * 1000)


def format_iso_utc(ts: float | str | datetime) -> str:
    """Format datetime as standard ISO 8601 UTC string: 2026-08-21T12:00:00Z."""
    dt = to_utc_datetime(ts)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_display_dt(ts: float | str | datetime, tz_offset_hours: float = 0.0) -> str:
    """Format timestamp for user display with optional local timezone offset."""
    dt = to_utc_datetime(ts)
    if tz_offset_hours != 0.0:
        dt = dt + timedelta(hours=tz_offset_hours)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def timeframe_to_ms(timeframe: str) -> int:
    """Get duration in milliseconds for a timeframe string (e.g. '15m' -> 900000)."""
    tf = timeframe.lower()
    if tf in TIMEFRAME_MS:
        return TIMEFRAME_MS[tf]

    # Handle custom intervals like '45m', '8h', '3d'
    unit = tf[-1]
    val = int(tf[:-1])
    if unit == "m":
        return val * 60 * 1000
    elif unit == "h":
        return val * 60 * 60 * 1000
    elif unit == "d":
        return val * 24 * 60 * 60 * 1000
    elif unit == "w":
        return val * 7 * 24 * 60 * 60 * 1000
    raise ValueError(f"Unsupported timeframe string: {timeframe}")


def timeframe_to_seconds(timeframe: str) -> int:
    """Get duration in seconds for a timeframe string."""
    return timeframe_to_ms(timeframe) // 1000


def align_timestamp_to_bar(ts_ms: int, timeframe: str) -> int:
    """Floor a millisecond timestamp to the start of its timeframe bar."""
    bar_ms = timeframe_to_ms(timeframe)
    return (ts_ms // bar_ms) * bar_ms


def calculate_bar_range(
    start_time: int | datetime,
    end_time: int | datetime,
    timeframe: str,
) -> tuple[int, int, int]:
    """Calculate normalized start_ms, end_ms and total expected bars."""
    start_ms = to_utc_ms(start_time)
    end_ms = to_utc_ms(end_time)
    bar_ms = timeframe_to_ms(timeframe)

    norm_start = (start_ms // bar_ms) * bar_ms
    norm_end = (end_ms // bar_ms) * bar_ms
    expected_bars = max(0, (norm_end - norm_start) // bar_ms + 1)
    return norm_start, norm_end, expected_bars
