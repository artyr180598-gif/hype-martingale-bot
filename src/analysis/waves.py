"""
Волновой и структурный анализ:
- Волны Эллиотта (импульс/коррекция, разметка по зигзагу с порогом)
- Режим волатильности (ATR-процентиль, squeeze BB/KC)
- Рыночная структура (тренд, зоны поддержки/сопротивления, BOS)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Wave:
    idx: int
    start_ts: int
    end_ts: int
    start_price: float
    end_price: float
    pct: float
    duration: int  # баров


@dataclass
class ElliottResult:
    waves: list[Wave]
    pattern: str            # impulse | correction | unclear
    wave_position: int      # 1..5, A/B/C, 0 если unclear
    trend_dir: int          # 1 up / -1 down
    target_zone: tuple[float, float] | None
    confidence: float       # 0..1
    note: str


@dataclass
class VolatilityState:
    atr_pct: float
    atr_pctl: float         # процентиль ATR за окно (0..1)
    rv_20: float            # реализованная волатильность, %
    bb_width_pctl: float
    squeeze: bool           # сжатие (BB внутри KC)
    state: str              # low | normal | high | extreme | squeeze
    state_ru: str


@dataclass
class StructureState:
    trend: str              # up | down | range
    strength: float         # 0..1 (по ADX)
    above_vwap: bool
    support: float | None   # ближайшая поддержка
    resistance: float | None  # ближайшее сопротивление
    last_swing_high: float | None
    last_swing_low: float | None
    adx: float
    plus_di: float
    minus_di: float


def _swing_points(high: pd.Series, low: pd.Series, left: int = 2, right: int = 2) -> tuple[list[int], list[int]]:
    """Индексы свинг-вершин (фракталы)."""
    highs: list[int] = []
    lows: list[int] = []
    h_vals, l_vals = high.values, low.values
    n = len(h_vals)
    for i in range(left, n - right):
        win_h = h_vals[i - left : i + right + 1]
        win_l = l_vals[i - left : i + right + 1]
        if h_vals[i] == win_h.max() and (
            h_vals[i] > win_h[win_h < h_vals[i]].max() if (win_h < h_vals[i]).any() else True
        ):
            highs.append(i)
        if l_vals[i] == win_l.min() and (
            l_vals[i] < win_l[win_l > l_vals[i]].min() if (win_l > l_vals[i]).any() else True
        ):
            lows.append(i)
    return highs, lows


def zigzag(
    df: pd.DataFrame,
    pct_threshold: float = 1.0,
    use_atr: bool = True,
    left: int = 2,
    right: int = 2,
) -> list[tuple[int, float]]:
    """
    Зигзаг: последовательность точек поворота [(индекс, цена)].
    Свинги чередуются; мелкие движения (меньше порога: pct % цены
    или 0.55×ATR при use_atr) отбрасываются.
    """
    close = df["close"]
    highs, lows = _swing_points(df["high"], df["low"], left, right)
    if not highs or not lows:
        return []
    atr_series = df["atr_14"] if "atr_14" in df.columns else None
    return _finalize_zigzag(df, highs, lows, atr_series, pct_threshold, use_atr)


def _finalize_zigzag(
    df: pd.DataFrame,
    highs: list[int],
    lows: list[int],
    atr_series: pd.Series | None,
    pct_threshold: float,
    use_atr: bool,
) -> list[tuple[int, float]]:
    """Собираем зигзаг: чередование свингов, отбрасываем мелкие (ниже порога)."""
    events = [(i, "H", float(df["high"].iloc[i])) for i in highs] + [
        (i, "L", float(df["low"].iloc[i])) for i in lows
    ]
    events.sort(key=lambda e: e[0])
    if not events:
        return []

    def threshold(i: int) -> float:
        if use_atr and atr_series is not None and i < len(atr_series):
            v = float(atr_series.iloc[i])
            if np.isfinite(v) and v > 0:
                return 0.55 * v  # минимум 0.55 ATR, либо pct от цены
        return pct_threshold / 100.0 * float(df["close"].iloc[i])

    points: list[tuple[int, float, str]] = []  # (idx, price, kind)
    for i, kind, price in events:
        if not points:
            points.append((i, price, kind))
            continue
        _, lp, last_kind = points[-1]
        if kind == last_kind:
            # обновляем экстремум того же типа
            if (kind == "H" and price > lp) or (kind == "L" and price < lp):
                points[-1] = (i, price, kind)
            continue
        # противоположный тип
        if (kind == "H" and price <= lp) or (kind == "L" and price >= lp):
            continue  # не образует нового экстремума
        th = threshold(i)
        if abs(price - lp) >= th or len(points) == 1:
            points.append((i, price, kind))
        # мелкие движения пропускаем (их экстремумы обновятся правилом выше)
    return [(i, p) for i, p, _ in points]


def elliott(df: pd.DataFrame, min_wave_pct: float = 0.35) -> ElliottResult:
    """
    Разметка волн по зигзагу: идентифицирует последнюю структуру
    (5-волновой импульс или A-B-C коррекцию) и зону цели.
    """
    zz = zigzag(df, pct_threshold=0.8, use_atr=True)
    waves: list[Wave] = []
    for k in range(len(zz) - 1):
        i0, p0 = zz[k]
        i1, p1 = zz[k + 1]
        pct = (p1 - p0) / p0 * 100
        if abs(pct) >= min_wave_pct:
            waves.append(
                Wave(
                    idx=k, start_ts=int(df["ts"].iloc[i0]), end_ts=int(df["ts"].iloc[i1]),
                    start_price=p0, end_price=p1, pct=pct, duration=int(i1 - i0),
                )
            )
    if len(waves) < 3:
        # недостаточно волн — используем последние движения как есть
        return ElliottResult(
            waves=waves, pattern="unclear", wave_position=0,
            trend_dir=1 if waves and waves[-1].pct > 0 else -1,
            target_zone=None, confidence=0.2,
            note="Недостаточно волновой структуры для разметки",
        )

    last5 = waves[-5:]
    dirs = [1 if w.pct > 0 else -1 for w in last5]
    n = len(last5)

    # Импульс: три однонаправленные волны с двумя коррекциями (5 волн)
    if n >= 5 and dirs[0] == dirs[2] == dirs[4] and dirs[1] == dirs[3] and dirs[0] != dirs[1]:
        trend_dir = dirs[0]
        w3 = last5[2]
        w5 = last5[4]
        # классическое правило: волна 3 не самая короткая
        impulse = [last5[0], last5[2], last5[4]]
        if w3.pct * trend_dir >= min(abs(w.pct) for w in impulse) * trend_dir:
            conf = 0.75
            pattern = "impulse"
            # цель коррекции A-B-C после импульса
            top = w5.end_price
            bottom = w5.start_price
            ext = 0.382 if conf else 0.5
            target_zone = (
                min(top, bottom) + (top - bottom) * trend_dir * -1 * 0.382,
                min(top, bottom) + (top - bottom) * trend_dir * -1 * 0.618,
            )
            target_zone = tuple(sorted(target_zone))  # type: ignore[assignment]
            return ElliottResult(
                waves=waves, pattern=pattern, wave_position=5, trend_dir=trend_dir,
                target_zone=target_zone, confidence=conf,
                note="Завершён 5-волновой импульс: ожидайте коррекцию A-B-C к 0.382–0.618",
            )
        return ElliottResult(
            waves=waves, pattern="impulse", wave_position=5, trend_dir=trend_dir,
            target_zone=None, confidence=0.45,
            note="5 волн, но волна 3 короче нормы — разметка ненадёжна",
        )

    # Коррекция A-B-C (3 волны): после неё ожидается продолжение тренда
    if n >= 3:
        a, b, c = last5[-3], last5[-2], last5[-1]
        dirs3 = [1 if w.pct > 0 else -1 for w in (a, b, c)]
        if dirs3[0] != dirs3[1] and dirs3[1] != dirs3[2]:
            # A и C в одну сторону
            trend_dir = -dirs3[2]  # тренд против коррекции
            abc_ext = (c.end_price - a.start_price) / a.start_price * 100
            conf = 0.6
            # цель: возобновление тренда за вершину B
            target = b.end_price if trend_dir > 0 else b.end_price
            target_zone = (target, a.end_price if trend_dir > 0 else c.end_price)
            return ElliottResult(
                waves=waves, pattern="correction", wave_position=3, trend_dir=trend_dir,
                target_zone=target_zone, confidence=conf,
                note="Коррекция A-B-C близка к завершению: ожидается продолжение тренда",
            )

    last_dir = dirs[-1]
    return ElliottResult(
        waves=waves, pattern="unclear", wave_position=0, trend_dir=last_dir,
        target_zone=None, confidence=0.3, note="Волновая структура неоднозначна",
    )


def volatility_state(df: pd.DataFrame) -> VolatilityState:
    """Режим волатильности на основе ATR, BB/KC и реализованной волатильности."""
    last = df.iloc[-1]
    atr_pct = float(last.get("atr_pct", np.nan))
    rv = float(last.get("rv_20", np.nan))
    atr_series = df["atr_pct"].dropna()
    pctl = 0.5
    if len(atr_series) > 10:
        pctl = float((atr_series <= atr_pct).mean())
    bb_w = df["bb_width"].dropna()
    bb_pctl = 0.5
    if len(bb_w) > 10:
        bb_pctl = float((bb_w <= float(last.get("bb_width", np.nan))).mean())

    bb_up, bb_low = float(last.get("bb_up", np.nan)), float(last.get("bb_low", np.nan))
    kc_up, kc_low = float(last.get("kc_up", np.nan)), float(last.get("kc_low", np.nan))
    squeeze = bool(bb_up <= kc_up and bb_low >= kc_low)

    if squeeze:
        state, state_ru = "squeeze", "Сжатие (squeeze)"
    elif pctl >= 0.85:
        state, state_ru = "extreme", "Экстремальная"
    elif pctl >= 0.65:
        state, state_ru = "high", "Высокая"
    elif pctl <= 0.25:
        state, state_ru = "low", "Низкая"
    else:
        state, state_ru = "normal", "Нормальная"

    return VolatilityState(
        atr_pct=atr_pct, atr_pctl=pctl, rv_20=rv, bb_width_pctl=bb_pctl,
        squeeze=squeeze, state=state, state_ru=state_ru,
    )


def market_structure(df: pd.DataFrame, lookback_swing: int = 60) -> StructureState:
    """Структура: тренд (EMA/ADX), уровни поддержки/сопротивления, VWAP."""
    last = df.iloc[-1]
    close = float(last["close"])
    adx_v = float(last.get("adx", np.nan))
    plus_di = float(last.get("plus_di", np.nan))
    minus_di = float(last.get("minus_di", np.nan))
    ema50 = float(last.get("ema_50", np.nan))
    ema200 = float(last.get("ema_200", np.nan))

    if adx_v >= 25 and plus_di > minus_di:
        trend = "up"
    elif adx_v >= 25 and minus_di > plus_di:
        trend = "down"
    else:
        trend = "range"

    window = df.tail(lookback_swing)
    sw_h = float(window["high"].max())
    sw_l = float(window["low"].min())

    above_vwap = close > float(last.get("vwap", np.nan)) if np.isfinite(last.get("vwap", np.nan)) else None  # type: ignore[arg-type]

    # ближайшие уровни по свингам
    zz = zigzag(df, pct_threshold=0.5, use_atr=True)
    levels_up = [p for _, p in zz if p > close * 1.001]
    levels_dn = [p for _, p in zz if p < close * 0.999]
    resistance = min(levels_up[-4:]) if levels_up else sw_h
    support = max(levels_dn[-4:]) if levels_dn else sw_l

    return StructureState(
        trend=trend,
        strength=min(1.0, adx_v / 45.0) if np.isfinite(adx_v) else 0.3,
        above_vwap=bool(above_vwap),
        support=support,
        resistance=resistance,
        last_swing_high=sw_h,
        last_swing_low=sw_l,
        adx=adx_v if np.isfinite(adx_v) else 0.0,
        plus_di=plus_di if np.isfinite(plus_di) else 0.0,
        minus_di=minus_di if np.isfinite(minus_di) else 0.0,
    )


@dataclass
class MomentumState:
    rsi: float
    macd_hist: float
    macd_cross: int  # 1 свежий бычий кросс, -1 медвежий, 0 нет
    stoch_k: float
    stoch_d: float
    obv_trend: int
    cvd_trend: int
    vol_z: float
    roc_20: float
    st_dir: int
    st_dist_pct: float


def momentum(df: pd.DataFrame) -> MomentumState:
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    macd_cross = 0
    if prev.get("macd") <= prev.get("macd_signal") and last.get("macd") > last.get("macd_signal"):
        macd_cross = 1
    elif prev.get("macd") >= prev.get("macd_signal") and last.get("macd") < last.get("macd_signal"):
        macd_cross = -1

    def trend_of(series: pd.Series, lookback: int = 10) -> int:
        s = series.dropna()
        if len(s) < lookback + 1:
            return 0
        return 1 if float(s.iloc[-1]) > float(s.iloc[-lookback]) else -1

    st_dir = int(last.get("st_dir", 1))
    st_trend = float(last.get("st_trend", np.nan))
    close = float(last["close"])
    dist = (close - st_trend) / close * 100 if np.isfinite(st_trend) and st_trend else 0.0

    return MomentumState(
        rsi=float(last.get("rsi_14", 50.0)),
        macd_hist=float(last.get("macd_hist", 0.0)),
        macd_cross=macd_cross,
        stoch_k=float(last.get("stoch_k", 50.0)),
        stoch_d=float(last.get("stoch_d", 50.0)),
        obv_trend=trend_of(df["obv"]),
        cvd_trend=trend_of(df["cvd"]),
        vol_z=float(last.get("vol_z", 0.0)),
        roc_20=float(last.get("roc_20", 0.0)),
        st_dir=st_dir,
        st_dist_pct=dist,
    )
