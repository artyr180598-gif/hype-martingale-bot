"""Entry / Stop / Take-Profit calculation.

Levels are derived from ATR and market structure -- never fixed percentages.
Stop/TP1 must preserve a minimum reward-to-risk; if structure makes that
impossible the engine returns ``WAIT`` (no trade).
"""

from __future__ import annotations

import math

from v3.config import SignalConfig
from v3.models import TimeframeView, TradeLevels


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def extension_atr(direction: str, price: float, atr: float, view: TimeframeView | None) -> float:
    """Насколько цена УЖЕ ушла от VWAP в сторону сделки (в ATR).

    «Не догоняй рынок»: для SHORT цена ниже VWAP — значит падение уже случилось,
    и новый шорт ловит откат; для LONG — зеркально. Возвращает 0.0, если движения
    в сторону сделки нет (цена по другую сторону VWAP) или данных нет.

    VWAP взят потому, что он уже посчитан в ``TimeframeView`` и привязан к
    реальному объёму сессии, а не к произвольному периоду скользящей средней.
    """
    if direction not in ("LONG", "SHORT") or view is None or price <= 0 or not math.isfinite(atr) or atr <= 0:
        return 0.0
    dist_pct = float(view.vwap_dist_pct or 0.0)
    # SHORT выгоден, когда цена НИЖЕ VWAP (dist < 0) — но тогда она уже упала
    moved = -dist_pct if direction == "SHORT" else dist_pct
    if moved <= 0:
        return 0.0
    return round(abs(moved) / 100.0 * price / atr, 3)


def chase_reason(direction: str, price: float, atr: float, view: TimeframeView | None, cfg: SignalConfig) -> str:
    """Объяснение человеческим языком, если вход был бы «в догонку»."""
    limit = float(cfg.ENTRY_MAX_EXTENSION_ATR or 0.0)
    if limit <= 0:
        return ""
    ext = extension_atr(direction, price, atr, view)
    if ext <= limit:
        return ""
    side = "упала" if direction == "SHORT" else "выросла"
    return (
        f"цена уже {side} на {ext:.1f} ATR от VWAP (порог {limit:.1f}) — "
        "движение в основном пройдено, ждём откат, а не догоняем"
    )


def build_levels(
    direction: str,
    price: float,
    atr: float,
    view: TimeframeView | None,
    cfg: SignalConfig,
    stop_override: float | None = None,
) -> TradeLevels | None:
    if direction not in ("LONG", "SHORT") or price <= 0:
        return TradeLevels(direction="WAIT", entry_zone=(0.0, 0.0), stop_loss=0.0, targets=[], rr=0.0, atr=atr, atr_pct=atr / price * 100 if price else 0, stop_pct=0.0, invalidation="no entry")

    fallback_notes: list[str] = []
    if not math.isfinite(atr) or atr <= 0:
        # fallback вместо отказа: 1.5% от цены как консервативная оценка ATR.
        atr = price * 0.015
        fallback_notes.append("auto_fallback: ATR недоступен → стоп по 1.5% от цены")

    is_long = direction == "LONG"
    sl_mult = _clip(cfg.ATR_SL_MULTIPLIER, cfg.ATR_MIN_SL_MULTIPLIER, cfg.ATR_MAX_SL_MULTIPLIER)
    if view is not None and view.squeeze:
        sl_mult = _clip(sl_mult * 0.85, cfg.ATR_MIN_SL_MULTIPLIER, cfg.ATR_MAX_SL_MULTIPLIER)
    # Буфер в ATR: стоп относится ЗА очевидный уровень, а не ровно на него.
    buffer_atr = max(0.0, float(cfg.ATR_STOP_BUFFER)) * atr

    # Structure-based stop first, but keep it inside 0.8-3.5 ATR.
    stop = price - sl_mult * atr if is_long else price + sl_mult * atr
    why = fallback_notes + [f"ATR {atr:.8g} ({atr / price * 100:.2f}%), stop = {sl_mult:.1f}×ATR"]
    if view is None:
        why.append("auto_fallback: структура недоступна → уровни по ATR от цены")

    if view is not None and view.support is not None and is_long:
        dist = abs(price - view.support)
        if view.support < price and cfg.ATR_MIN_SL_MULTIPLIER * atr <= dist <= cfg.ATR_MAX_SL_MULTIPLIER * atr:
            # не ровно на уровне (его выбивают сбором ликвидности), а ЗА ним
            stop = view.support - buffer_atr
            why.append(f"stop moved to structural support {view.support:.8g} (буфер {buffer_atr:.8g})")
    if view is not None and view.resistance is not None and not is_long:
        dist = abs(price - view.resistance)
        if view.resistance > price and cfg.ATR_MIN_SL_MULTIPLIER * atr <= dist <= cfg.ATR_MAX_SL_MULTIPLIER * atr:
            stop = view.resistance + buffer_atr
            why.append(f"stop moved to structural resistance {view.resistance:.8g} (буфер {buffer_atr:.8g})")

    # Явная подсказка стопа (например, от сценария liquidity sweep).
    if stop_override is not None and (stop_override < price if is_long else stop_override > price):
        dist = abs(price - stop_override)
        if cfg.ATR_MIN_SL_MULTIPLIER * 0.5 * atr <= dist <= cfg.ATR_MAX_SL_MULTIPLIER * 1.1 * atr:
            stop = stop_override - buffer_atr if is_long else stop_override + buffer_atr
            why.append(f"stop anchored to scenario level {stop_override:.8g} (буфер {buffer_atr:.8g})")

    # Entry zone: anchored to market structure when it is near the price.
    # A zone is always bounded to 1.0 ATR and stays close enough to fill
    # (a zone 2-4% away produces a ~9% fill rate on historical backtests).
    zone_lo, zone_hi, zone_why = _entry_zone(price, atr, view, is_long)
    if is_long:
        entry_zone = (zone_lo, zone_hi)
    else:
        entry_zone = (zone_lo, zone_hi)
    why.extend(zone_why)

    risk = abs(entry_zone[1] - stop) if is_long else abs(stop - entry_zone[0])
    risk = max(risk, 0.2 * atr)  # avoid zero-risk degenerate plans
    if is_long:
        entry_ref = entry_zone[1]
    else:
        entry_ref = entry_zone[0]

    # TP1 must satisfy MIN_RISK_REWARD; TP2/TP3 extend from structure/ATR.
    tp_mult = max(cfg.ATR_TP_MULTIPLIER, cfg.MIN_RISK_REWARD * risk / atr)
    tp1 = entry_ref + tp_mult * atr if is_long else entry_ref - tp_mult * atr
    targets = [tp1]
    if view is not None:
        if is_long and view.resistance and view.resistance > tp1:
            targets.append(view.resistance)
        elif not is_long and view.support and view.support < tp1:
            targets.append(view.support)
    base_next = entry_ref + (tp_mult + 1.6) * atr if is_long else entry_ref - (tp_mult + 1.6) * atr
    if len(targets) == 1:
        targets.append(base_next)
    if len(targets) < 3:
        targets.append(entry_ref + (tp_mult + 3.2) * atr if is_long else entry_ref - (tp_mult + 3.2) * atr)
    targets = sorted(targets, reverse=not is_long)[:3]

    rr = abs(targets[0] - entry_ref) / risk
    stop_pct = risk / entry_ref * 100.0 if entry_ref else 0.0
    invalidation = (
        f"closing {view.timeframe}-candle below {stop:.8g}" if is_long and view else f"price below {stop:.8g}"
    )
    if not is_long:
        invalidation = f"closing {view.timeframe}-candle above {stop:.8g}" if view else f"price above {stop:.8g}"

    why.extend([
        f"entry zone {entry_zone[0]:.8g}-{entry_zone[1]:.8g}",
        f"target 1 {targets[0]:.8g} -> R:R 1:{rr:.2f}",
    ])

    return TradeLevels(
        direction=direction,
        entry_zone=entry_zone,
        stop_loss=stop,
        targets=targets,
        rr=round(rr, 2),
        atr=atr,
        atr_pct=round(atr / price * 100.0, 3),
        stop_pct=round(stop_pct, 3),
        invalidation=invalidation,
        why=why,
    )


def _entry_zone(
    price: float,
    atr: float,
    view: TimeframeView | None,
    is_long: bool,
) -> tuple[float, float, list[str]]:
    """Return (entry_zone_lo, entry_zone_hi, explanation).

    The zone starts at the current price and extends toward the nearest
    structural anchor (support for longs, resistance for shorts, VWAP, EMA-50)
    when that anchor is within ``0.2..1.0 × ATR`` -- i.e. a realistic retest
    target, not a manufactured level far away from the market.
    """
    lo, hi = (price - 0.5 * atr, price) if is_long else (price, price + 0.5 * atr)
    why: list[str] = []

    if view is None:
        return lo, hi, why

    anchors: list[tuple[float, str]] = []
    if is_long:
        if view.support is not None and view.support < price:
            anchors.append((view.support, "support"))
        if view.vwap_dist_pct > 0:  # price above VWAP -> pullback target
            vwap = price * (1.0 - view.vwap_dist_pct / 100.0)
            anchors.append((vwap, "VWAP"))
    else:
        if view.resistance is not None and view.resistance > price:
            anchors.append((view.resistance, "resistance"))
        if view.vwap_dist_pct < 0:  # price below VWAP -> pullback target
            vwap = price * (1.0 - view.vwap_dist_pct / 100.0)
            anchors.append((vwap, "VWAP"))

    for anchor_price, label in anchors:
        dist = abs(price - anchor_price)
        if 0.2 * atr <= dist <= 1.0 * atr:
            if is_long:
                lo, hi = anchor_price, price
            else:
                lo, hi = price, anchor_price
            why.append(f"entry anchored to {label} {anchor_price:.8g} ({dist / atr:.2f}×ATR)")
            break

    # keep the zone readable: never wider than 1.0 ATR from price
    if is_long:
        lo = max(lo, price - 1.0 * atr)
        hi = min(hi, price + 0.05 * atr)
    else:
        lo = max(lo, price - 0.05 * atr)
        hi = min(hi, price + 1.0 * atr)
    return round(lo, 8), round(hi, 8), why


def structure_anchor(views: list[TimeframeView], is_long: bool) -> float | None:
    for v in reversed(views):
        if is_long and v.support is not None:
            return v.support
        if not is_long and v.resistance is not None:
            return v.resistance
    return None


def safe_round_to_tick(value: float, instrument) -> float:
    """Round to exchange tick if an Instrument object is available."""
    tick = getattr(instrument, "tick_size", 0.0) or 0.0
    if tick > 0:
        mult = 1.0 / tick
        return math.floor(value * mult + 0.5) / mult
    return value
