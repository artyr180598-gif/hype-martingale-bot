"""Форматирование чисел для вывода (Telegram / CLI / дашборд)."""

from __future__ import annotations

import math


def fmt_price(price: float, scale: int | None = None) -> str:
    """Цена: столько знаков, сколько реально нужно для этого инструмента."""
    if price is None or not math.isfinite(price):
        return "—"
    p = abs(price)
    if scale is not None:
        return f"{price:,.{scale}f}".replace(",", " ")
    if p >= 1000:
        return f"{price:,.2f}".replace(",", " ")
    if p >= 1:
        return f"{price:.4f}"
    if p >= 0.01:
        return f"{price:.5f}"
    if p >= 0.0001:
        return f"{price:.6f}"
    return f"{price:.10f}"


def fmt_qty(qty: float, scale: int | None = None) -> str:
    if qty is None or not math.isfinite(qty):
        return "—"
    if scale is not None:
        return f"{qty:.{scale}f}"
    if abs(qty) >= 1000:
        return f"{qty:,.1f}".replace(",", " ")
    if abs(qty) >= 1:
        return f"{qty:.4f}"
    if abs(qty) >= 0.01:
        return f"{qty:.5f}"
    return f"{qty:.8f}"


def fmt_usd(value: float, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    a = abs(value)
    sign = "-" if value < 0 else ""
    if a >= 1_000_000_000:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1_000_000:
        return f"{sign}${a / 1e6:.2f}M"
    if a >= 10_000:
        return f"{sign}${a:,.0f}".replace(",", " ")
    if a >= 100:
        return f"{sign}${a:,.{min(digits, 2)}f}".replace(",", " ")
    return f"{sign}${a:.{max(digits, 2)}f}"


def fmt_pct(value: float, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:+.{digits}f}%"


def fmt_int(value: float) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{int(round(value)):,}".replace(",", " ")
