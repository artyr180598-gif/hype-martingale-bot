from dataclasses import dataclass
from datetime import datetime, timezone
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


def normalize_bybit_kline(row: list[object], symbol: str, timeframe: str) -> Candle:
    """Normalize Bybit kline rows into a venue-independent candle.

    Bybit returns newest-first rows; ordering is intentionally left to the
    dataset manager so this function has no hidden temporal behavior.
    """
    if len(row) < 7:
        raise ValueError("invalid_bybit_kline_row")
    open_ms = int(row[0])
    close_ms = int(row[6])
    open_price = Decimal(str(row[1]))
    high = Decimal(str(row[2]))
    low = Decimal(str(row[3]))
    close = Decimal(str(row[4]))
    volume = Decimal(str(row[5]))
    quote_volume = Decimal(str(row[6])) if len(row) < 8 else Decimal(str(row[7]))
    if not (low <= open_price <= high and low <= close <= high):
        raise ValueError("invalid_ohlc_relationship")
    if volume < 0 or quote_volume < 0:
        raise ValueError("negative_volume")
    return Candle(
        symbol=symbol.upper(), timeframe=timeframe,
        open_time=datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc),
        close_time=datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc),
        open=open_price, high=high, low=low, close=close,
        volume=volume, quote_volume=quote_volume,
        trade_count=int(row[8]) if len(row) > 8 and row[8] is not None else None,
    )
