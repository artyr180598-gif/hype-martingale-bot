"""«Намечающееся движение» (emergence) — ранний отбор монет до разгона.

Проблема, которую решаем: старый heat-сканер на 45% состоял из 24h-изменения,
поэтому бот ловил монеты УЖЕ после движения (chase-эффект). Этот модуль
детектирует признаки, которые обычно появляются ЗА 15–60 минут до импульса:

  * ``rvol``            — объём проснулся (последний бар >> собственного среднего);
  * ``squeeze_release`` — сжатие волатильности, полосы начинают расширяться;
  * ``consolidation``   — узкий диапазон при нормальном ATR (накопление);
  * ``near_breakout``   — цена у вершины 24h-диапазона при нарастающем объёме;
  * ``near_breakdown``  — зеркально у дна;
  * ``oi_build_pct``    — OI растёт, а цена ещё спокойна (позиционирование).

Итог — ``ignition`` 0..100 (подогрев) и ``early_direction`` (подсказка
направления). ВАЖНО: это признак РАНЖИРОВАНИЯ и объяснения, а не триггер и не
гейт — направление всегда остаётся за ``FuturesSignalEngine``, поэтому
инварианты платформы (детерминированный гейт, AI только объясняет) не
нарушаются.
"""

from __future__ import annotations

import pandas as pd

from src.data.indicators import compute_all
from v3.config import SignalConfig
from v3.models import EmergenceSnapshot


def _safe(value: float, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if v == v else default  # NaN -> default
    except (TypeError, ValueError):
        return default


def relative_volume(df: pd.DataFrame, window: int = 20) -> float:
    """RVOL: объём последнего закрытого бара / среднее за окно."""
    try:
        vol = pd.to_numeric(df["volume"], errors="coerce").dropna()
        if len(vol) < max(5, window // 2):
            return 1.0
        avg = float(vol.tail(window).mean())
        return float(vol.iloc[-1] / avg) if avg > 0 else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def squeeze_state(fe: pd.DataFrame, lookback: int = 8) -> tuple[bool, bool]:
    """(сжатие сейчас, сжатие было недавно и полосы расширяются)."""
    if fe.empty:
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
    """Узкий диапазон за последние бары относительно ATR (накопление)."""
    if len(fe) < bars + 2:
        return False
    tail = fe.tail(bars)
    high = float(pd.to_numeric(tail["high"], errors="coerce").max())
    low = float(pd.to_numeric(tail["low"], errors="coerce").min())
    atr = _safe(fe["atr_14"].iloc[-1])
    if atr <= 0:
        return False
    return (high - low) <= atr_mult * atr


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
    squeeze_now, squeeze_release = squeeze_state(fe, cfg.EMERGENCE_SQUEEZE_LOOKBACK)
    consolidated = is_consolidated(fe, cfg.EMERGENCE_CONSOLIDATION_BARS, cfg.EMERGENCE_CONSOLIDATION_ATR)
    sma20 = _safe(fe["ema_20"].iloc[-1], close)
    atr = _safe(fe["atr_14"].iloc[-1], close * 0.01)

    # позиция в 24h-диапазоне (0..1)
    hl = high_24h or float(pd.to_numeric(df["high"], errors="coerce").max())
    ll = low_24h or float(pd.to_numeric(df["low"], errors="coerce").min())
    rng = hl - ll
    dpos = (close - ll) / rng if rng > 0 else 0.5
    dpos = max(0.0, min(1.0, dpos))

    rs24 = (price_24h_pct or 0.0) - (btc_24h_pct or 0.0)

    near_breakout = bool(dpos >= 0.78 and (hl - close) <= 1.0 * atr and rvol >= 1.2)
    near_breakdown = bool(dpos <= 0.22 and (close - ll) <= 1.0 * atr and rvol >= 1.2)

    funding_neutral = funding_rate is None or abs(_safe(funding_rate)) <= cfg.FUNDING_OVERHEATED * 0.5

    ignition = 0.0
    notes: list[str] = []

    # 1) объём проснулся (простые слова — это текст для новичка)
    if rvol >= cfg.EMERGENCE_RVOL_MIN:
        ignition += 25.0
        notes.append(f"объём заметно выше обычного (×{rvol:.1f}) — кто-то активно заходит")
    elif rvol > 1.0:
        ignition += min(10.0, (rvol - 1.0) * 15.0)

    # 2) сжатие/выход из сжатия
    if squeeze_release:
        ignition += 25.0
        notes.append("волатильность сжималась и теперь расширяется — часто перед резким движением")
    elif squeeze_now:
        ignition += 10.0
        notes.append("волатильность сжалась: цена «затихла» перед возможным импульсом")

    # 3) консолидация (накопление)
    if consolidated:
        ignition += 15.0
        notes.append("цена ходит в узком коридоре (накопление)")

    # 4) близость к экстремуму диапазона
    if near_breakout:
        ignition += 15.0
        notes.append("цена у верхней границы дневного диапазона, объём растёт")
    elif near_breakdown:
        ignition += 15.0
        notes.append("цена у нижней границы дневного диапазона, продажи нарастают")

    # 5) позиционирование: OI растёт, цена ещё спокойна
    oi_build = None
    if oi_delta_pct is not None:
        oi_build = float(oi_delta_pct)
        if oi_build >= cfg.OI_CHANGE_BUILD_PCT and abs(price_24h_pct or 0.0) <= cfg.POSITIONING_QUIET_PRICE_CHANGE_PCT:
            ignition += 10.0
            notes.append(f"открытые позиции растут (+{oi_build:.1f}%), а цена спокойна — кто-то готовится")

    pct = price_24h_pct or 0.0

    # 6) нейтральность фандинга и относительная сила (ранний, не разогретый вход)
    if funding_neutral:
        ignition += 5.0
    if rs24 > 3.0 and 0.30 <= dpos <= 0.80:
        ignition += 8.0
        notes.append(f"сильнее BTC ({rs24:+.1f}%), но не на вершине диапазона")
    elif rs24 < -3.0 and 0.20 <= dpos <= 0.70:
        ignition += 8.0
        notes.append(f"слабее BTC ({rs24:+.1f}%) — кандидат на разворот вниз")

    # 7) АНТИ-chase: уже разогрето у экстремума → снижаем подогрев
    if dpos >= 0.95 and pct >= 14.0:
        ignition -= 18.0
        notes.append("уже у вершины после большого хода — не «намечается», а разогрето")
    elif dpos >= 0.85 and pct >= 10.0:
        ignition -= 10.0
        notes.append("близко к вершине после заметного хода — часть движения уже состоялась")
    elif dpos <= 0.05 and pct <= -14.0:
        ignition -= 18.0
        notes.append("уже у дна после сильного падения — не «намечается»")
    elif dpos <= 0.15 and pct <= -10.0:
        ignition -= 10.0
        notes.append("близко к дну после заметного падения")

    ignition = max(0.0, min(100.0, ignition))

    # направление-подсказка (НЕ сигнал): только контекст для ранжирования
    early_direction = "FLAT"
    above_ema = close > sma20
    below_ema = close < sma20
    if near_breakout or (above_ema and (squeeze_release or consolidated) and dpos >= 0.55 and rvol >= 1.2):
        early_direction = "LONG"
    elif near_breakdown or (below_ema and (squeeze_release or consolidated) and dpos <= 0.45 and rvol >= 1.2):
        early_direction = "SHORT"

    return EmergenceSnapshot(
        enabled=True,
        rvol=round(rvol, 3),
        squeeze=squeeze_now,
        squeeze_release=squeeze_release,
        consolidation=consolidated,
        rs24=round(rs24, 3),
        dpos=round(dpos, 3),
        oi_build_pct=round(oi_build, 3) if oi_build is not None else None,
        funding_neutral=funding_neutral,
        near_breakout=near_breakout,
        near_breakdown=near_breakdown,
        ignition=round(ignition, 1),
        early_direction=early_direction,
        notes=notes,
    )
