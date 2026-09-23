"""Risk engine — ATR SL/TP, R:R, position sizing, liquidation (ported from Trading_Signal_Bot + freqtrade)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import Settings


@dataclass
class RiskPlan:
    entry: float
    stop_loss: float
    take_profits: list[float]  # TP1, TP2, TP3
    take_profit_pcts: list[float]  # close %
    stop_distance_pct: float
    stop_distance_atr: float
    risk_reward: float
    risk_reward_tp1: float
    position_size_pct: float
    leverage: int
    liquidation_price: float | None
    invalidation: float
    entry_zone_low: float
    entry_zone_high: float
    expected_move_pct: float
    expected_move_atr: float
    risk_usd: float | None = None
    position_usd: float | None = None


def calculate_risk_plan(
    entry: float,
    atr: float,
    direction: str,
    cfg: Settings,
    support: float | None = None,
    resistance: float | None = None,
    swing_low: float | None = None,
    swing_high: float | None = None,
    account_balance: float = 1000.0,
) -> RiskPlan:
    """
    Calculate full risk plan with ATR-based SL/TP.
    Logic:
    - SL = entry ± ATR * multiplier, buffered beyond structure level
    - TP1 = 1R, TP2 = 2R, TP3 = 3.2R (configurable)
    - Entry zone = VWAP / support ± 0.3 ATR
    - Expected move = ATR * TP multiplier
    """
    is_long = direction.upper() == "LONG"

    # Base SL distance in ATR
    sl_atr = cfg.ATR_SL_MULTIPLIER
    # Adjust for structure
    if is_long and support:
        # SL should be below support with buffer
        struct_sl = entry - (entry - support) - atr * cfg.ATR_STOP_BUFFER
        # Take the lower of ATR SL and structure SL, but not too wide
        atr_sl_price = entry - atr * sl_atr
        sl_price = min(atr_sl_price, struct_sl)
    elif not is_long and resistance:
        struct_sl = entry + (resistance - entry) + atr * cfg.ATR_STOP_BUFFER
        atr_sl_price = entry + atr * sl_atr
        sl_price = max(atr_sl_price, struct_sl)
    else:
        sl_price = entry - atr * sl_atr if is_long else entry + atr * sl_atr

    # Clamp SL distance
    sl_dist = abs(entry - sl_price)
    sl_dist_atr = sl_dist / atr if atr > 0 else sl_atr
    if sl_dist_atr < cfg.ATR_MIN_SL_MULTIPLIER:
        sl_dist = atr * cfg.ATR_MIN_SL_MULTIPLIER
        sl_price = entry - sl_dist if is_long else entry + sl_dist
    elif sl_dist_atr > cfg.ATR_MAX_SL_MULTIPLIER:
        sl_dist = atr * cfg.ATR_MAX_SL_MULTIPLIER
        sl_price = entry - sl_dist if is_long else entry + sl_dist

    sl_dist_pct = sl_dist / entry * 100 if entry else 0
    sl_dist_atr = sl_dist / atr if atr else 0

    # Take profits based on R
    tp1_dist = sl_dist * cfg.TP1_R
    tp2_dist = sl_dist * cfg.TP2_R
    tp3_dist = sl_dist * cfg.TP3_R

    if is_long:
        tp1 = entry + tp1_dist
        tp2 = entry + tp2_dist
        tp3 = entry + tp3_dist
    else:
        tp1 = entry - tp1_dist
        tp2 = entry - tp2_dist
        tp3 = entry - tp3_dist

    # R:R for TP1 (minimum)
    risk_reward = tp1_dist / sl_dist if sl_dist else 0
    # Overall R:R to TP3
    risk_reward_tp3 = tp3_dist / sl_dist if sl_dist else 0

    # Entry zone (0.3 ATR around entry, anchored to VWAP/support)
    entry_zone_half = atr * 0.3
    entry_zone_low = entry - entry_zone_half
    entry_zone_high = entry + entry_zone_half

    # Expected move
    expected_move_atr = cfg.ATR_TP_MULTIPLIER
    expected_move = atr * expected_move_atr
    expected_move_pct = expected_move / entry * 100 if entry else 0

    # Position sizing: risk % of account
    risk_pct = cfg.RISK_PER_TRADE_PCT / 100
    risk_usd = account_balance * risk_pct
    # Position size = risk_usd / sl_dist_pct
    if sl_dist_pct > 0:
        position_usd = risk_usd / (sl_dist_pct / 100)
        # Cap by max position %
        max_pos_usd = account_balance * cfg.MAX_POSITION_PCT / 100
        position_usd = min(position_usd, max_pos_usd)
        position_size_pct = position_usd / account_balance * 100
    else:
        position_usd = 0
        position_size_pct = 0

    # Leverage (inverse of SL distance, capped)
    # Simple: if SL 2% -> leverage ~5x max 10x
    if sl_dist_pct > 0:
        lev = min(cfg.MAX_LEVERAGE, max(1, int(10 / sl_dist_pct * 2)))
    else:
        lev = 1

    # Liquidation estimate (simplified: for isolated margin, liq ~ entry * (1 - 1/lev) for long)
    liquidation = None
    if lev > 1:
        if is_long:
            liquidation = entry * (1 - 0.9 / lev)  # 90% of margin
        else:
            liquidation = entry * (1 + 0.9 / lev)

    # Invalidation (structure break)
    invalidation = swing_low if is_long else swing_high
    if not invalidation:
        invalidation = sl_price

    return RiskPlan(
        entry=entry,
        stop_loss=sl_price,
        take_profits=[tp1, tp2, tp3],
        take_profit_pcts=list(cfg.TP_CLOSE_PCT),
        stop_distance_pct=sl_dist_pct,
        stop_distance_atr=sl_dist_atr,
        risk_reward=risk_reward_tp3,
        risk_reward_tp1=tp1_dist / sl_dist if sl_dist else 0,
        position_size_pct=position_size_pct,
        leverage=lev,
        liquidation_price=liquidation,
        invalidation=invalidation,
        entry_zone_low=entry_zone_low,
        entry_zone_high=entry_zone_high,
        expected_move_pct=expected_move_pct,
        expected_move_atr=expected_move_atr,
        risk_usd=risk_usd,
        position_usd=position_usd,
    )


def risk_score(risk_plan: RiskPlan, cfg: Settings, spread_pct: float | None = None) -> int:
    """
    Risk score 0-10, lower is better.
    Factors: SL distance, R:R, spread, leverage.
    """
    score = 0

    # SL distance
    if risk_plan.stop_distance_pct > 5:
        score += 3
    elif risk_plan.stop_distance_pct > 3:
        score += 1

    # R:R
    if risk_plan.risk_reward < 1.5:
        score += 3
    elif risk_plan.risk_reward < 2.0:
        score += 1

    # Spread
    if spread_pct and spread_pct > 0.3:
        score += 2
    elif spread_pct and spread_pct > 0.15:
        score += 1

    # Leverage
    if risk_plan.leverage > 8:
        score += 2
    elif risk_plan.leverage > 5:
        score += 1

    return min(10, score)
