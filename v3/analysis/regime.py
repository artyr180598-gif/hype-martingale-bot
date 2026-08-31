"""Market regime detection.

Regime is NOT a trade signal -- it only changes how other components interpret
the data (trend-following in trends, mean-reversion hygiene in ranges, etc.).
The classifier is deterministic and tested on synthetic series.
"""

from __future__ import annotations

from v3.config import SignalConfig
from v3.models import RegimeSnapshot, TimeframeView


def detect_regime(views: list[TimeframeView], cfg: SignalConfig) -> RegimeSnapshot:
    if not views:
        return RegimeSnapshot(regime="UNCERTAIN", note="no timeframe data")

    weighted_trend = 0.0
    total_weight = 0.0
    directions: list[str] = []
    for i, v in enumerate(views):
        # slower timeframes get more weight for the macro picture
        weight = 1.0 + i * 0.35
        total_weight += weight
        directions.append(v.trend)
        if v.trend == "up":
            weighted_trend += weight
        elif v.trend == "down":
            weighted_trend -= weight

    trend_score = weighted_trend / total_weight if total_weight else 0.0
    up_count = sum(1 for d in directions if d == "up")
    down_count = sum(1 for d in directions if d == "down")
    range_count = sum(1 for d in directions if d == "range")

    entry = views[0]
    macro = views[-1]
    adx = entry.adx
    atr_pct = entry.atr_pct
    atr_pctl = entry.atr_pctl
    squeeze = entry.squeeze
    vol_z = entry.vol_z

    direction = "up" if trend_score > 0.35 else "down" if trend_score < -0.35 else "flat"
    strength = min(1.0, abs(trend_score))

    conflicts: list[str] = []
    if up_count and down_count:
        conflicts.append("trend conflict between timeframes")
    if macro.trend == "up" and entry.trend == "down":
        conflicts.append("macro up / entry down (counter-trend)")
    if macro.trend == "down" and entry.trend == "up":
        conflicts.append("macro down / entry up (counter-trend)")

    alignment = [v.timeframe for v in views if v.trend == direction] if direction != "flat" else []

    vol_state = (
        "extreme" if atr_pct >= cfg.ATR_PCT_EXTREME
        else "high" if atr_pct >= cfg.ATR_PCT_HIGH
        else "low" if atr_pctl <= 0.25
        else "normal"
    )

    if len(views) >= 2:
        breakout = any(v.squeeze for v in views[-2:]) and vol_z > cfg.VOLUME_Z_BULL
        # price expansion after squeeze with above-average volume
        if entry.squeeze and abs(entry.vwap_dist_pct) > 0.3 and vol_z > 0:
            regime = "BREAKOUT" if entry.trend == "up" else "BREAKDOWN"
        elif direction == "up" and up_count >= max(2, len(views) - 1) and adx >= cfg.ADX_TREND_MIN:
            regime = "TRENDING_UP"
        elif direction == "down" and down_count >= max(2, len(views) - 1) and adx >= cfg.ADX_TREND_MIN:
            regime = "TRENDING_DOWN"
        elif vol_state == "extreme":
            regime = "HIGH_VOLATILITY"
        elif vol_state == "low" and range_count >= max(2, len(views) - 1):
            regime = "LOW_VOLATILITY"
        elif range_count >= max(2, len(views) - 1):
            regime = "RANGING"
        elif vol_z > 1.2 and up_count >= down_count and direction != "down":
            regime = "ACCUMULATION"
        elif vol_z < -1.2 and down_count >= up_count and direction != "up":
            regime = "DISTRIBUTION"
        else:
            regime = "UNCERTAIN"
    else:
        regime = "UNCERTAIN"

    confidence = strength
    if conflicts:
        confidence = max(0.15, confidence - 0.25)

    note = (
        f"{regime}; trend_score={trend_score:+.2f}; adx={adx:.0f}; "
        f"atr={atr_pct:.2f}%; vol_z={vol_z:+.1f}; alignment={','.join(alignment) or 'none'}"
    )
    return RegimeSnapshot(
        regime=regime,
        direction=direction,
        volatility_state=vol_state,
        strength=round(strength, 3),
        trend_alignment=alignment,
        conflicts=conflicts,
        confidence=round(confidence, 3),
        note=note,
    )
