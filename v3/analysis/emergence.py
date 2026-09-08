"""Ранний детектор импульса — до того, как монета стала «горячей».

Важно разделять три состояния, которые часто смешивают простые сканеры:

* ``EARLY`` — база сжимается, объём/давление начинают просыпаться, но пробой
  ещё не убежал;
* ``TRIGGERED`` — закрытый бар уже подтвердил выход из коридора, но движение
  всё ещё находится в допустимом расстоянии от базы;
* ``EXHAUSTED`` — цена уже слишком далеко у экстремума. Это не кандидат «до
  движения», даже если 24h-процент выглядит впечатляюще.

Модуль использует только закрытые бары, которые ему передали. Он не создаёт
сигнал и не меняет направление движка: snapshot нужен для ранжирования,
объяснения и отдельного списка наблюдения.
"""

from __future__ import annotations

import math

import pandas as pd

from src.data.indicators import compute_all
from v3.config import SignalConfig
from v3.models import EmergenceSnapshot


def _safe(value: float, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def relative_volume(df: pd.DataFrame, window: int = 20) -> float:
    """RVOL последнего закрытого бара / среднего *предыдущих* баров.

    Среднее намеренно не включает текущий бар. Иначе именно всплеск объёма,
    который мы пытаемся обнаружить, сам разбавляет свою базу и становится
    менее заметным.
    """
    try:
        vol = pd.to_numeric(df["volume"], errors="coerce").dropna()
        if len(vol) < max(5, window // 2 + 1):
            return 1.0
        current = _safe(vol.iloc[-1])
        baseline = vol.iloc[:-1].tail(window)
        avg = _safe(baseline.mean())
        return current / avg if avg > 0 else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def _volume_acceleration(df: pd.DataFrame, window: int) -> float:
    """Ускорение объёма: RVOL текущего бара относительно предыдущего."""
    try:
        vol = pd.to_numeric(df["volume"], errors="coerce").dropna()
        if len(vol) < max(8, window // 2 + 2):
            return 1.0
        current_base = _safe(vol.iloc[:-1].tail(window).mean())
        previous_base = _safe(vol.iloc[:-2].tail(window).mean())
        if current_base <= 0 or previous_base <= 0:
            return 1.0
        return _safe((vol.iloc[-1] / current_base) / (vol.iloc[-2] / previous_base), 1.0)
    except Exception:  # noqa: BLE001
        return 1.0


def squeeze_state(fe: pd.DataFrame, lookback: int = 8) -> tuple[bool, bool]:
    """Вернуть ``(сжатие сейчас, недавний выход из сжатия)``."""
    if fe.empty or "squeeze" not in fe or "bb_width" not in fe:
        return False, False
    squeeze_now = bool(fe["squeeze"].iloc[-1])
    recent = fe["squeeze"].tail(lookback + 1).tolist()
    was_squeezed = any(bool(x) for x in recent[:-1])
    width = pd.to_numeric(fe["bb_width"], errors="coerce")
    release = False
    if was_squeezed and not squeeze_now and len(width) >= 2:
        w_last, w_prev = _safe(width.iloc[-1]), _safe(width.iloc[-2])
        release = w_last > w_prev > 0
    return squeeze_now, release


def is_consolidated(fe: pd.DataFrame, bars: int = 12, atr_mult: float = 1.6) -> bool:
    """Узкий коридор перед последним баром относительно текущего ATR."""
    if len(fe) < bars + 2:
        return False
    # Последний бар может быть стартом импульса; база должна считаться до него.
    tail = fe.iloc[-bars - 1 : -1]
    high = _safe(pd.to_numeric(tail["high"], errors="coerce").max())
    low = _safe(pd.to_numeric(tail["low"], errors="coerce").min())
    atr = _safe(fe["atr_14"].iloc[-1])
    if atr <= 0:
        return False
    return (high - low) <= atr_mult * atr


def _compression_ratio(fe: pd.DataFrame) -> float:
    """Текущий ATR к медианному ATR прошлого окна, без текущего значения."""
    try:
        atrs = pd.to_numeric(fe["atr_14"], errors="coerce").dropna()
        if len(atrs) < 20:
            return 1.0
        current = _safe(atrs.iloc[-1])
        typical = _safe(atrs.iloc[:-1].tail(80).median())
        return current / typical if typical > 0 else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def _range_width_atr(fe: pd.DataFrame, bars: int, atr: float) -> float:
    if atr <= 0 or len(fe) < bars + 1:
        return 0.0
    base = fe.iloc[-bars - 1 : -1]
    high = _safe(pd.to_numeric(base["high"], errors="coerce").max())
    low = _safe(pd.to_numeric(base["low"], errors="coerce").min())
    return max(0.0, (high - low) / atr)


def _breakout_levels(
    fe: pd.DataFrame,
    lookback: int,
    atr: float,
    close: float,
    max_distance_atr: float,
) -> tuple[bool, bool]:
    """Проверить пробой прошлого коридора текущим закрытием.

    Дистанция ограничена конфигом: закрытие на несколько ATR выше базы — уже
    погоня, а не ранний триггер.
    """
    if atr <= 0 or len(fe) < lookback + 2:
        return False, False
    base = fe.iloc[-lookback - 1 : -1]
    high = _safe(pd.to_numeric(base["high"], errors="coerce").max())
    low = _safe(pd.to_numeric(base["low"], errors="coerce").min())
    distance = max(0.0, float(max_distance_atr)) * atr
    return (
        close > high and (close - high) <= distance,
        close < low and (low - close) <= distance,
    )


def _breakout_pressure(fe: pd.DataFrame, atr: float, close: float) -> float:
    """Давление последнего бара в диапазоне [-1, +1].

    В отличие от одного зелёного/красного бара учитываются тело, положение
    закрытия внутри свечи и согласованность последних закрытий.
    """
    if fe.empty:
        return 0.0
    last = fe.iloc[-1]
    high = _safe(last.get("high"), close)
    low = _safe(last.get("low"), close)
    open_ = _safe(last.get("open"), close)
    bar_range = max(high - low, abs(close) * 1e-8, atr * 0.01)
    body = _clip((close - open_) / bar_range)
    close_location = _clip(2.0 * (close - low) / bar_range - 1.0)

    consistency = 0.0
    if len(fe) >= 4:
        previous = _safe(fe["close"].iloc[-4], close)
        delta = (close - previous) / max(atr * 1.5, abs(close) * 1e-8)
        consistency = _clip(delta)
    return round(_clip(0.50 * body + 0.35 * close_location + 0.15 * consistency), 3)


def _series_delta(fe: pd.DataFrame, column: str, bars: int = 3) -> float:
    """Изменение индикатора за несколько ЗАКРЫТЫХ баров."""
    try:
        values = pd.to_numeric(fe[column], errors="coerce").dropna()
        if len(values) <= bars:
            return 0.0
        return _safe(values.iloc[-1] - values.iloc[-1 - bars])
    except Exception:  # noqa: BLE001
        return 0.0


def _cycle_scores(
    fe: pd.DataFrame,
    *,
    rvol: float,
    volume_acceleration: float,
    pressure: float,
    squeeze_now: bool,
    squeeze_release: bool,
    consolidated: bool,
    near_breakout: bool,
    near_breakdown: bool,
    breakout_up: bool,
    breakout_down: bool,
) -> tuple[float, float, list[str], list[str]]:
    """Симметричная оценка зарождения цикла вверх/вниз.

    Она не пытается угадать будущее одной свечой. Баллы требуют согласования
    нескольких независимых групп: база/сжатие, ранний объём, разворот MACD и
    RSI, EMA, DI, OBV/CVD и давление закрытий. Все расчёты используют только
    переданные (в live-пути уже закрытые) бары.
    """
    last = fe.iloc[-1]
    long_score = 0.0
    short_score = 0.0
    long_notes: list[str] = []
    short_notes: list[str] = []

    # Нейтральная «пружина» повышает готовность обеих сторон, но сама не
    # выбирает направление.
    if squeeze_now or consolidated:
        long_score += 8.0
        short_score += 8.0
    if squeeze_release:
        long_score += 7.0
        short_score += 7.0
    if rvol >= 1.2:
        participation = min(10.0, 4.0 + (rvol - 1.2) * 6.0)
        long_score += participation
        short_score += participation
    if volume_acceleration >= 1.20:
        long_score += 4.0
        short_score += 4.0

    macd = _safe(last.get("macd_hist"))
    macd_delta = _series_delta(fe, "macd_hist", 3)
    if macd_delta > 0:
        long_score += 14.0
        long_notes.append("импульс MACD разворачивается вверх")
        if macd > 0:
            long_score += 5.0
    elif macd_delta < 0:
        short_score += 14.0
        short_notes.append("импульс MACD разворачивается вниз")
        if macd < 0:
            short_score += 5.0

    rsi = _safe(last.get("rsi_14"), 50.0)
    rsi_delta = _series_delta(fe, "rsi_14", 3)
    if rsi_delta >= 2.0 and 38.0 <= rsi <= 68.0:
        long_score += 12.0
        long_notes.append("RSI растёт без перекупленности")
    elif rsi_delta <= -2.0 and 32.0 <= rsi <= 62.0:
        short_score += 12.0
        short_notes.append("RSI снижается без перепроданности")

    ema9 = _safe(last.get("ema_9"))
    ema20 = _safe(last.get("ema_20"))
    ema_gap = ema9 - ema20
    try:
        prev = fe.iloc[-4]
        old_gap = _safe(prev.get("ema_9")) - _safe(prev.get("ema_20"))
    except Exception:  # noqa: BLE001
        old_gap = ema_gap
    if ema_gap > 0 or ema_gap > old_gap:
        long_score += 13.0
        long_notes.append("быстрая EMA пересекает или догоняет медленную снизу")
    if ema_gap < 0 or ema_gap < old_gap:
        short_score += 13.0
        short_notes.append("быстрая EMA пересекает или догоняет медленную сверху")

    plus_di = _safe(last.get("plus_di"))
    minus_di = _safe(last.get("minus_di"))
    if plus_di > minus_di:
        long_score += 10.0
    elif minus_di > plus_di:
        short_score += 10.0

    obv_delta = _series_delta(fe, "obv", 5)
    cvd_delta = _series_delta(fe, "cvd", 5)
    if obv_delta > 0:
        long_score += 6.0
    elif obv_delta < 0:
        short_score += 6.0
    if cvd_delta > 0:
        long_score += 7.0
        long_notes.append("объёмный поток подтверждает покупателей")
    elif cvd_delta < 0:
        short_score += 7.0
        short_notes.append("объёмный поток подтверждает продавцов")

    if pressure >= 0.20:
        long_score += 10.0
    elif pressure <= -0.20:
        short_score += 10.0
    if near_breakout:
        long_score += 8.0
    if near_breakdown:
        short_score += 8.0
    if breakout_up:
        long_score += 10.0
    if breakout_down:
        short_score += 10.0

    return (
        round(min(100.0, long_score), 1),
        round(min(100.0, short_score), 1),
        long_notes,
        short_notes,
    )


def detect_emergence(
    df: pd.DataFrame,
    *,
    price_24h_pct: float = 0.0,
    high_24h: float | None = None,
    low_24h: float | None = None,
    btc_24h_pct: float | None = None,
    oi_delta_pct: float | None = None,
    funding_rate: float | None = None,
    cfg: SignalConfig | None = None,
) -> EmergenceSnapshot:
    cfg = cfg or SignalConfig()
    if df is None or len(df) < 30:
        return EmergenceSnapshot(enabled=False, notes=["insufficient klines for emergence"])

    fe = compute_all(df)
    close = _safe(fe["close"].iloc[-1])
    if close <= 0:
        return EmergenceSnapshot(enabled=False, notes=["no valid price for emergence"])

    rvol = relative_volume(df, cfg.EMERGENCE_RVOL_WINDOW)
    volume_acceleration = _volume_acceleration(df, cfg.EMERGENCE_RVOL_WINDOW)
    squeeze_now, squeeze_release = squeeze_state(fe, cfg.EMERGENCE_SQUEEZE_LOOKBACK)
    atr = _safe(fe["atr_14"].iloc[-1], close * 0.01)
    compression_ratio = _compression_ratio(fe)
    compressed_by_atr = compression_ratio <= cfg.EMERGENCE_COMPRESSION_ATR_RATIO
    consolidated = is_consolidated(fe, cfg.EMERGENCE_CONSOLIDATION_BARS, cfg.EMERGENCE_CONSOLIDATION_ATR)
    range_width_atr = _range_width_atr(fe, cfg.EMERGENCE_CONSOLIDATION_BARS, atr)
    pressure = _breakout_pressure(fe, atr, close)

    # Позиция внутри реального 24h-диапазона. При отсутствии тикера берём
    # только переданные свечи, не подставляем синтетические значения.
    hl = _safe(high_24h) if high_24h and high_24h > 0 else _safe(pd.to_numeric(df["high"], errors="coerce").max())
    ll = _safe(low_24h) if low_24h and low_24h > 0 else _safe(pd.to_numeric(df["low"], errors="coerce").min())
    rng = hl - ll
    dpos = _clip((close - ll) / rng, 0.0, 1.0) if rng > 0 else 0.5
    rs24 = (price_24h_pct or 0.0) - (btc_24h_pct or 0.0)

    breakout_up, breakout_down = _breakout_levels(
        fe, cfg.EMERGENCE_BREAKOUT_LOOKBACK, atr, close, cfg.EMERGENCE_MAX_TRIGGER_ATR
    )
    near_breakout = bool(
        dpos >= 0.78
        and rvol >= 1.2
        and pressure >= cfg.EMERGENCE_MIN_BREAKOUT_PRESSURE * 0.6
    )
    near_breakdown = bool(
        dpos <= 0.22
        and rvol >= 1.2
        and pressure <= -cfg.EMERGENCE_MIN_BREAKOUT_PRESSURE * 0.6
    )

    base = fe.iloc[-cfg.EMERGENCE_BREAKOUT_LOOKBACK - 1 : -1]
    base_high = _safe(pd.to_numeric(base["high"], errors="coerce").max(), close)
    base_low = _safe(pd.to_numeric(base["low"], errors="coerce").min(), close)

    long_score, short_score, long_cycle_notes, short_cycle_notes = _cycle_scores(
        fe,
        rvol=rvol,
        volume_acceleration=volume_acceleration,
        pressure=pressure,
        squeeze_now=squeeze_now,
        squeeze_release=squeeze_release,
        consolidated=consolidated,
        near_breakout=near_breakout,
        near_breakdown=near_breakdown,
        breakout_up=breakout_up,
        breakout_down=breakout_down,
    )
    best_cycle_score = max(long_score, short_score)
    bias_margin = abs(long_score - short_score)

    oi_build = None
    if oi_delta_pct is not None and _safe(oi_delta_pct, float("nan")) == _safe(oi_delta_pct):
        oi_build = float(oi_delta_pct)
    funding_neutral = funding_rate is None or abs(_safe(funding_rate)) <= cfg.FUNDING_OVERHEATED * 0.5

    # Сначала определяем сторону: она нужна только для проверки согласованности
    # признаков. Это НЕ торговое решение.
    early_direction = "FLAT"
    if breakout_up and pressure >= cfg.EMERGENCE_MIN_BREAKOUT_PRESSURE:
        early_direction = "LONG"
    elif breakout_down and pressure <= -cfg.EMERGENCE_MIN_BREAKOUT_PRESSURE:
        early_direction = "SHORT"
    elif (
        best_cycle_score >= cfg.CYCLE_DIRECTION_SCORE_MIN
        and bias_margin >= cfg.CYCLE_BIAS_MARGIN_MIN
    ):
        early_direction = "LONG" if long_score > short_score else "SHORT"
    elif pressure >= cfg.EMERGENCE_MIN_BREAKOUT_PRESSURE and dpos >= 0.45 and (squeeze_now or squeeze_release or consolidated):
        early_direction = "LONG"
    elif pressure <= -cfg.EMERGENCE_MIN_BREAKOUT_PRESSURE and dpos <= 0.55 and (squeeze_now or squeeze_release or consolidated):
        early_direction = "SHORT"
    elif near_breakout:
        early_direction = "LONG"
    elif near_breakdown:
        early_direction = "SHORT"

    room_pct = (1.0 - dpos) if early_direction == "LONG" else dpos if early_direction == "SHORT" else max(dpos, 1.0 - dpos)
    recent_move_atr = 0.0
    if len(fe) >= 5 and atr > 0:
        recent_move_atr = abs(close - _safe(fe["close"].iloc[-5])) / atr

    ignition = 0.0
    notes: list[str] = []

    # 1. Объём: база без текущего бара, плюс ускорение.
    if rvol >= cfg.EMERGENCE_RVOL_MIN:
        ignition += 25.0
        notes.append(f"объём заметно выше обычного (×{rvol:.1f}) — кто-то активно заходит")
    elif rvol > 1.0:
        ignition += min(10.0, (rvol - 1.0) * 15.0)
    if volume_acceleration >= 1.35:
        ignition += 5.0
        notes.append("объём ускоряется относительно предыдущего бара")

    # 2. Сжатие — обязательный «до импульса» контекст, а не просто высокий ATR.
    if squeeze_release:
        ignition += 25.0
        notes.append("волатильность сжималась и теперь расширяется — часто перед резким движением")
    elif squeeze_now or compressed_by_atr:
        ignition += 10.0
        if squeeze_now:
            notes.append("волатильность сжалась: цена «затихла» перед возможным импульсом")
        elif compressed_by_atr:
            notes.append(f"волатильность ниже обычной (≈{compression_ratio:.2f} нормы)")

    # 3. База и место для хода.
    if consolidated:
        ignition += 15.0
        notes.append("цена ходит в узком коридоре (накопление)")
    if near_breakout and early_direction == "LONG":
        ignition += 15.0
        notes.append("цена у верхней границы диапазона, покупатели давят")
    elif near_breakdown and early_direction == "SHORT":
        ignition += 15.0
        notes.append("цена у нижней границы диапазона, продавцы давят")

    if breakout_up and early_direction == "LONG":
        ignition += 18.0
        notes.append("последняя закрытая свеча вышла выше коридора — импульс подтверждается")
    elif breakout_down and early_direction == "SHORT":
        ignition += 18.0
        notes.append("последняя закрытая свеча вышла ниже коридора — импульс подтверждается")

    # 4. OI × цена и funding.
    if oi_build is not None and oi_build >= cfg.OI_CHANGE_BUILD_PCT and abs(price_24h_pct or 0.0) <= cfg.POSITIONING_QUIET_PRICE_CHANGE_PCT:
        ignition += 10.0
        notes.append(f"открытые позиции растут (+{oi_build:.1f}%), а цена спокойна — кто-то готовится")
    if funding_neutral:
        ignition += 5.0

    # 5. Давление свечи и относительная сила. Направление обязано совпадать;
    # просто «монета зелёная за 24h» больше не считается ранним импульсом.
    aligned_pressure = (early_direction == "LONG" and pressure > 0) or (early_direction == "SHORT" and pressure < 0)
    if aligned_pressure and abs(pressure) >= 0.25:
        ignition += 8.0
        notes.append("закрытие бара показывает направленное давление покупателей" if early_direction == "LONG" else "закрытие бара показывает направленное давление продавцов")
    if early_direction == "LONG" and rs24 > 3.0 and room_pct >= cfg.EMERGENCE_MIN_ROOM_PCT:
        ignition += 8.0
        notes.append(f"сильнее BTC ({rs24:+.1f}%), но запас до границы ещё есть")
    elif early_direction == "SHORT" and rs24 < -3.0 and room_pct >= cfg.EMERGENCE_MIN_ROOM_PCT:
        ignition += 8.0
        notes.append(f"слабее BTC ({rs24:+.1f}%), но до нижней границы ещё есть место")

    selected_cycle_notes = long_cycle_notes if early_direction == "LONG" else short_cycle_notes
    if early_direction in ("LONG", "SHORT") and selected_cycle_notes:
        notes.extend(selected_cycle_notes[:3])

    # Анти-chase: отделяем «движение начинается» от «движение уже выжато».
    exhausted = False
    pct = price_24h_pct or 0.0
    if (dpos >= 0.95 and pct >= 8.0) or (dpos <= 0.05 and pct <= -8.0):
        exhausted = True
        ignition -= 22.0
        notes.append("уже у вершины/дна после большого хода — это разогрето, не ранний вход")
    elif recent_move_atr > cfg.EMERGENCE_MAX_RECENT_MOVE_ATR and abs(pct) >= 6.0:
        exhausted = True
        ignition -= 16.0
        notes.append("последние бары уже прошли слишком далеко — импульс частично состоялся")
    elif dpos >= 0.85 and pct >= 10.0:
        ignition -= 10.0
        notes.append("близко к вершине после заметного хода — часть движения уже состоялась")
    elif dpos <= 0.15 and pct <= -10.0:
        ignition -= 10.0
        notes.append("близко ко дну после заметного хода — часть движения уже состоялась")

    ignition = max(0.0, min(100.0, ignition))
    if not exhausted and early_direction in ("LONG", "SHORT"):
        # Cycle score способен увидеть поворот до пробоя; ignition остаётся
        # общей силой ранних признаков, поэтому берём более сильную оценку.
        ignition = max(ignition, best_cycle_score)
    triggered = (breakout_up and early_direction == "LONG") or (breakout_down and early_direction == "SHORT")
    if exhausted:
        phase = "EXHAUSTED"
    elif triggered:
        phase = "TRIGGERED"
    elif ignition >= cfg.EMERGENCE_IGNITION_MIN:
        phase = "EARLY"
    else:
        phase = "NEUTRAL"

    if exhausted:
        cycle_stage = "EXHAUSTED"
    elif triggered:
        cycle_stage = "CONFIRMED"
    elif early_direction in ("LONG", "SHORT") and best_cycle_score >= cfg.CYCLE_TRADE_SCORE_MIN:
        cycle_stage = "TURNING"
    elif early_direction in ("LONG", "SHORT"):
        cycle_stage = "BUILDING"
    else:
        cycle_stage = "NEUTRAL"

    trigger_price = base_high if early_direction == "LONG" else base_low if early_direction == "SHORT" else 0.0
    invalidation_price = base_low if early_direction == "LONG" else base_high if early_direction == "SHORT" else 0.0

    return EmergenceSnapshot(
        enabled=True,
        rvol=round(rvol, 3),
        volume_acceleration=round(volume_acceleration, 3),
        squeeze=squeeze_now,
        squeeze_release=squeeze_release,
        consolidation=consolidated,
        compression_ratio=round(compression_ratio, 3),
        range_width_atr=round(range_width_atr, 3),
        breakout_pressure=round(pressure, 3),
        rs24=round(rs24, 3),
        dpos=round(dpos, 3),
        room_pct=round(room_pct, 3),
        oi_build_pct=round(oi_build, 3) if oi_build is not None else None,
        funding_neutral=funding_neutral,
        near_breakout=near_breakout,
        near_breakdown=near_breakdown,
        phase=phase,
        ignition=round(ignition, 1),
        early_direction=early_direction,
        long_score=long_score,
        short_score=short_score,
        cycle_score=round(best_cycle_score, 1),
        bias_margin=round(bias_margin, 1),
        cycle_stage=cycle_stage,
        trigger_price=round(trigger_price, 8),
        invalidation_price=round(invalidation_price, 8),
        notes=notes,
    )
