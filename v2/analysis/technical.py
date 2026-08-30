"""
Технический анализ v2.

Три обязательных среза (по ТЗ):
  1. Тренд на H1 — ADX/DI+/DI−: есть ли тренд и куда он направлен;
  2. Накопление на M15 — наклон OBV + дивергенция OBV/цены + всплески объёма;
  3. Уровни Фибоначчи — по последнему значимому свингу.

Плюс ATR (база для динамических стопов) и VWAP (справедливая цена сессии).

Модуль ничего не знает о сети: на вход — свечи, на выход — TechnicalReport.
Поэтому его можно гонять и на демо-данных, и на истории из бэктеста.
"""

from __future__ import annotations

import numpy as np

from v2.analysis import indicators as ind
from v2.config import V2Config
from v2.core.errors import InsufficientData
from v2.core.logging import get_logger
from v2.models import (
    AccumulationSnapshot,
    Candle,
    FibSnapshot,
    TechnicalReport,
    TrendSnapshot,
)

logger = get_logger("analysis.technical")

MIN_BARS = 40


def analyze_trend(candles: list[Candle], timeframe: str, atr_period: int = 14) -> tuple[TrendSnapshot, float]:
    """ADX-тренд на заданном ТФ. Возвращает (снимок, ATR в единицах цены)."""
    if len(candles) < MIN_BARS:
        raise InsufficientData(f"{timeframe}: нужно ≥ {MIN_BARS} свечей, получено {len(candles)}")

    h = np.array([c.high for c in candles], dtype=float)
    lo = np.array([c.low for c in candles], dtype=float)
    c = np.array([c.close for c in candles], dtype=float)

    di = ind.adx(h, lo, c, atr_period)
    adx_value = ind.last(di["adx"])
    plus_di = ind.last(di["plus_di"])
    minus_di = ind.last(di["minus_di"])
    direction = ind.trend_direction(plus_di, minus_di, adx_value)
    strength = ind.trend_strength(adx_value)

    atr_series = ind.atr(h, lo, c, atr_period)
    atr_value = ind.last(atr_series)
    atr_pct = (atr_value / c[-1] * 100.0) if c[-1] > 0 else 0.0

    snap = TrendSnapshot(
        timeframe=timeframe,
        adx=round(adx_value, 1),
        plus_di=round(plus_di, 1),
        minus_di=round(minus_di, 1),
        ema_fast=round(ind.last(ind.ema(c, 20)), 8),
        ema_slow=round(ind.last(ind.ema(c, 50)), 8),
        rsi=round(ind.last(ind.rsi(c, 14)), 1),
        atr=round(atr_value, 8),
        atr_pct=round(atr_pct, 3),
        direction=direction,
        strength=strength,
    )

    ru = {"up": "восходящий", "down": "нисходящий", "flat": "боковой"}[direction]
    ru_strength = {
        "strong": "сильный",
        "moderate": "умеренный",
        "weak": "слабый",
        "none": "отсутствует",
    }[strength]
    snap.note = (
        f"ADX {snap.adx:.0f} на {timeframe}: тренд {ru_strength} и {ru} "
        f"(DI+ {snap.plus_di:.0f} vs DI− {snap.minus_di:.0f})"
    )
    return snap, atr_value


def analyze_accumulation(candles: list[Candle], timeframe: str, period: int = 20) -> AccumulationSnapshot:
    """OBV-накопление на младшем ТФ."""
    if len(candles) < period + 5:
        raise InsufficientData(f"{timeframe}: мало свечей для OBV")

    c = np.array([x.close for x in candles], dtype=float)
    v = np.array([x.volume for x in candles], dtype=float)

    obv_series = ind.obv(c, v)
    slope = ind.obv_slope(obv_series, period)

    # Дивергенция: сравниваем прирост OBV и прирост цены за одно и то же окно
    price_change = (c[-1] - c[-period]) / c[-period] if c[-period] != 0 else 0.0
    obv_window = obv_series[-period:]
    denom = np.nanmean(np.abs(obv_series)) or 1.0
    obv_change = (obv_window[-1] - obv_window[0]) / denom
    divergence = float(obv_change - price_change)

    vz = ind.volume_zscore(v, period)
    vol_z = float(vz[-1]) if len(vz) else 0.0

    snap = AccumulationSnapshot(
        timeframe=timeframe,
        obv_slope=round(slope, 3),
        obv_divergence=round(divergence, 3),
        volume_zscore=round(vol_z, 2),
    )
    snap.accumulation = slope > 0.15 and divergence > -0.1
    snap.distribution = slope < -0.15

    if snap.accumulation:
        snap.note = (
            f"OBV на {timeframe} растёт (наклон {slope:+.2f}) при цене {price_change*100:+.1f}% — "
            "объём опережает цену, похоже на накопление"
        )
    elif snap.distribution:
        snap.note = f"OBV на {timeframe} снижается (наклон {slope:+.2f}) — идёт разгрузка/распределение"
    else:
        snap.note = f"OBV на {timeframe} без выраженной динамики (наклон {slope:+.2f}) — флэт по объёму"
    if abs(vol_z) >= 2:
        snap.note += f"; объём последнего бара аномальный (z={vol_z:+.1f})"
    return snap


def analyze_fib(candles: list[Candle], direction: int = 1) -> FibSnapshot:
    """Фибо по последнему значимому свингу."""
    h = np.array([c.high for c in candles], dtype=float)
    lo = np.array([c.low for c in candles], dtype=float)

    swing_high = ind.last_swing(h, lo, direction=1, left=3, right=3)
    swing_low = ind.last_swing(h, lo, direction=-1, left=3, right=3)
    if swing_high is None:
        swing_high = float(np.nanmax(h))
    if swing_low is None:
        swing_low = float(np.nanmin(lo))
    if swing_high <= swing_low:
        swing_high, swing_low = swing_low * 1.01, swing_low

    levels = ind.fib_levels(swing_low, swing_high, direction)
    return FibSnapshot(
        swing_low=round(swing_low, 8),
        swing_high=round(swing_high, 8),
        direction=direction,
        retracements={_fib_key(k): round(v, 8) for k, v in levels["retracements"].items()},
        extensions={_fib_key(k): round(v, 8) for k, v in levels["extensions"].items()},
    )


def _fib_key(level: float) -> str:
    """0.500 → '0.5', 1.000 → '1.0': ключи фибо без лишних нулей."""
    text = f"{level:.3f}"
    if "." in text:
        text = text.rstrip("0") or "0"
        if text.endswith("."):
            text += "0"
    return text


def build_technical_report(
    candles_by_tf: dict[str, list[Candle]],
    config: V2Config,
    *,
    trend_tf: str | None = None,
    accum_tf: str | None = None,
    direction_hint: int = 1,
) -> TechnicalReport:
    """
    Собирает TechnicalReport из свечей нескольких таймфреймов.

    candles_by_tf = {"1h": [...], "15m": [...]}. Если какого-то ТФ нет —
    раздел помечается как degraded, но отчёт всё равно строится (частичные
    данные лучше, чем никакой ответ).
    """
    trend_tf = trend_tf or config.ANALYSIS_TREND_TF
    accum_tf = accum_tf or config.ANALYSIS_ACCUM_TF
    report = TechnicalReport()
    notes: list[str] = []
    degraded: list[str] = []

    # ── тренд + ATR ──────────────────────────────────────────────
    trend_candles = candles_by_tf.get(trend_tf) or []
    if trend_candles:
        try:
            report.trend, atr_value = analyze_trend(trend_candles, trend_tf, config.ATR_PERIOD)
            report.atr = round(atr_value, 8)
            report.atr_pct = report.trend.atr_pct
            report.price = float(trend_candles[-1].close)
            report.vwap = round(
                ind.last(
                    ind.vwap(
                        [c.high for c in trend_candles],
                        [c.low for c in trend_candles],
                        [c.close for c in trend_candles],
                        [c.volume for c in trend_candles],
                    )
                ),
                8,
            )
            notes.append(report.trend.note)
            if len(trend_candles) >= 96:
                report.change_24h_pct = round(
                    (trend_candles[-1].close / trend_candles[-96].close - 1) * 100, 2
                )
        except InsufficientData as exc:
            degraded.append(f"тренд {trend_tf}: {exc}")
    else:
        degraded.append(f"нет свечей {trend_tf}")

    # ── накопление ───────────────────────────────────────────────
    accum_candles = candles_by_tf.get(accum_tf) or []
    if accum_candles:
        try:
            report.accumulation = analyze_accumulation(accum_candles, accum_tf)
            notes.append(report.accumulation.note)
        except InsufficientData as exc:
            degraded.append(f"OBV {accum_tf}: {exc}")
    else:
        degraded.append(f"нет свечей {accum_tf}")

    # ── фибо ─────────────────────────────────────────────────────
    base = trend_candles or accum_candles
    if base:
        report.fib = analyze_fib(base, direction_hint)
        if report.fib.retracements:
            notes.append(
                f"Свинг {report.fib.swing_low:.8g} → {report.fib.swing_high:.8g}; "
                f"ключевой ретрейсмент 0.618 = {report.fib.retracements.get('0.618', 0):.8g}"
            )

    # ── интегральная оценка 0..100 ───────────────────────────────
    score = 50.0
    if report.trend.direction == "up":
        score += 12 + min(13, report.trend.adx / 4)
    elif report.trend.direction == "down":
        score -= 12 + min(13, report.trend.adx / 4)
    else:
        score -= 5
    if report.accumulation.accumulation:
        score += 10
    if report.accumulation.distribution:
        score -= 10
    if report.trend.rsi >= 75:
        score -= 8
        notes.append(f"RSI {report.trend.rsi:.0f} — перегрев, риск отката")
    elif report.trend.rsi <= 30:
        score += 4
        notes.append(f"RSI {report.trend.rsi:.0f} — перепроданность, возможен отскок")
    if report.atr_pct > 12:
        score -= 6
        notes.append(f"ATR {report.atr_pct:.1f}% от цены — экстремальная волатильность, стоп будет широким")
    if report.price and report.vwap and report.price > report.vwap:
        score += 3
    report.score = float(np.clip(score, 0.0, 100.0))
    report.notes = notes
    report.degraded = degraded
    return report
