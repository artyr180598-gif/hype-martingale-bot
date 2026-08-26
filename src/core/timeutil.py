"""Утилиты времени (UTC)."""

from __future__ import annotations

from datetime import datetime, timezone

TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}

TIMEFRAME_LABEL: dict[str, str] = {
    "1m": "1 мин",
    "3m": "3 мин",
    "5m": "5 мин",
    "15m": "15 мин",
    "30m": "30 мин",
    "1h": "1 час",
    "2h": "2 часа",
    "4h": "4 часа",
    "6h": "6 часов",
    "12h": "12 часов",
    "1d": "1 день",
}


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def tf_ms(timeframe: str) -> int:
    tf = timeframe.lower()
    if tf not in TIMEFRAME_MS:
        raise ValueError(f"Неизвестный таймфрейм: {timeframe}")
    return TIMEFRAME_MS[tf]


def tf_label(timeframe: str) -> str:
    return TIMEFRAME_LABEL.get(timeframe.lower(), timeframe)


def bar_start_ms(ts_ms: int, timeframe: str) -> int:
    """Начало бара, к которому относится timestamp."""
    step = tf_ms(timeframe)
    return (ts_ms // step) * step


def fmt_ts(ts_ms: int | None) -> str:
    if not ts_ms:
        return "—"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fmt_dt_short(ts_ms: int | None) -> str:
    if not ts_ms:
        return "—"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%d.%m %H:%M")
