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


def build_levels(
    direction: str,
    price: float,
    atr: float,
    view: TimeframeView | None,
    cfg: SignalConfig,
) -> TradeLevels | None:
    if direction not in ("LONG", "SHORT") or atr <= 0 or price <= 0:
        return TradeLevels(direction="WAIT", entry_zone=(0.0, 0.0), stop_loss=0.0, targets=[], rr=0.0, atr=atr, atr_pct=atr / price * 100 if price else 0, stop_pct=0.0, invalidation="no entry")

    is_long = direction == "LONG"
    sl_mult = _clip(cfg.ATR_SL_MULTIPLIER, cfg.ATR_MIN_SL_MULTIPLIER, cfg.ATR_MAX_SL_MULTIPLIER)
    if view is not None and view.squeeze:
        sl_mult = _clip(sl_mult * 0.85, cfg.ATR_MIN_SL_MULTIPLIER, cfg.ATR_MAX_SL_MULTIPLIER)

    # Structure-based stop first, but keep it inside 0.8-3.5 ATR.
    stop = price - sl_mult * atr if is_long else price + sl_mult * atr
    why = [f"ATR {atr:.8g} ({atr / price * 100:.2f}%), stop = {sl_mult:.1f}×ATR"]

    if view is not None and view.support is not None and is_long:
        dist = abs(price - view.support)
        if view.support < price and cfg.ATR_MIN_SL_MULTIPLIER * atr <= dist <= cfg.ATR_MAX_SL_MULTIPLIER * atr:
            stop = view.support
            why.append(f"stop moved to structural support {view.support:.8g}")
    if view is not None and view.resistance is not None and not is_long:
        dist = abs(price - view.resistance)
        if view.resistance > price and cfg.ATR_MIN_SL_MULTIPLIER * atr <= dist <= cfg.ATR_MAX_SL_MULTIPLIER * atr:
            stop = view.resistance
            why.append(f"stop moved to structural resistance {view.resistance:.8g}")

    # Entry zone: around price, pulled toward the market so limit orders can fill
    # (a zone 2-4% away produces a ~9% fill rate on historical backtests).
    if is_long:
        entry_zone = (price - 0.5 * atr, price)
    else:
        entry_zone = (price, price + 0.5 * atr)

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
