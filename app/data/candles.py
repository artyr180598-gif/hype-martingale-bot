from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int | None


def _timeframe_delta(timeframe: str) -> timedelta:
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    if timeframe.isdigit():
        return timedelta(minutes=int(timeframe))
    suffix = timeframe[-1].lower()
    if suffix not in units or not timeframe[:-1].isdigit():
        raise ValueError(f"unsupported_timeframe:{timeframe}")
    return timedelta(seconds=int(timeframe[:-1]) * units[suffix])


def normalize_bybit_kline(row: list[object], symbol: str, timeframe: str) -> Candle:
    """Normalize Bybit [start, O, H, L, C, volume, turnover] rows."""
    if len(row) < 7:
        raise ValueError("invalid_bybit_kline_row")
    open_ms = int(row[0])
    open_price = Decimal(str(row[1]))
    high = Decimal(str(row[2]))
    low = Decimal(str(row[3]))
    close = Decimal(str(row[4]))
    volume = Decimal(str(row[5]))
    quote_volume = Decimal(str(row[6]))
    if not (low <= open_price <= high and low <= close <= high):
        raise ValueError("invalid_ohlc_relationship")
    if volume < 0 or quote_volume < 0:
        raise ValueError("negative_volume")
    open_time = datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc)
    return Candle(
        symbol=symbol.upper(), timeframe=timeframe,
        open_time=open_time, close_time=open_time + _timeframe_delta(timeframe),
        open=open_price, high=high, low=low, close=close,
        volume=volume, quote_volume=quote_volume, trade_count=None,
    )
