"""Bot confidence scoring — 6 independent analyses (ported from v3 confidence.py)."""

from __future__ import annotations

from ..config import Settings


def calculate_bot_confidence(
    quality_score: float,
    data_completeness: float,
    timeframe_agreement: float,
    volume_orderbook_score: float,
    risk_score: int,
    rr: float,
    impulse_score: float,
    cfg: Settings,
) -> dict:
    """
    Calculate bot confidence 0-100% from 6 independent analyses.
    Weights configurable via BOT_CONFIDENCE_WEIGHTS.
    """
    # Normalize components to 0-100
    # Quality: 0-100 already
    quality_norm = max(0, min(100, quality_score))

    # Data: 0..1 -> 0-100, plus freshness bonus
    data_norm = max(0, min(100, data_completeness * 100))

    # Trend agreement: 0-100 (how many TFs agree)
    trend_norm = max(0, min(100, timeframe_agreement * 100 if timeframe_agreement <= 1 else timeframe_agreement))

    # Volume/orderbook: 0-100
    confirm_norm = max(0, min(100, volume_orderbook_score))

    # Risk: inverse of risk_score 0-10 -> 100-0
    # Lower risk score = higher confidence
    risk_norm = max(0, 100 - risk_score * 10)
    # Bonus for good RR
    if rr >= 3.0:
        risk_norm = min(100, risk_norm + 10)
    elif rr >= 2.5:
        risk_norm = min(100, risk_norm + 5)

    # Impulse: 0-100
    impulse_norm = max(0, min(100, impulse_score))

    weights = cfg.bot_confidence_weights

    confidence = (
        quality_norm * weights.get("quality", 0.30)
        + data_norm * weights.get("data", 0.15)
        + trend_norm * weights.get("trend", 0.20)
        + confirm_norm * weights.get("confirm", 0.15)
        + risk_norm * weights.get("risk", 0.10)
        + impulse_norm * weights.get("impulse", 0.10)
    )

    # Label
    if confidence >= cfg.BOT_CONFIDENCE_HIGH_MIN:
        label = "высокая"
    elif confidence >= cfg.BOT_CONFIDENCE_MEDIUM_MIN:
        label = "умеренная"
    elif confidence >= cfg.BOT_CONFIDENCE_LOW_MIN:
        label = "низкая"
    else:
        label = "очень низкая"

    # Breakdown for UI
    breakdown = [
        {"name": "Качество сетапа", "value": quality_norm, "weight": weights.get("quality", 0.30), "desc": f"оценка сетапа {quality_norm:.0f}/100"},
        {"name": "Свежесть и полнота данных", "value": data_norm, "weight": weights.get("data", 0.15), "desc": f"данные {data_norm:.0f}%"},
        {"name": "Согласованность таймфреймов", "value": trend_norm, "weight": weights.get("trend", 0.20), "desc": f"{trend_norm:.0f}% TF в сторону сделки"},
        {"name": "Объём, стакан и позиции", "value": confirm_norm, "weight": weights.get("confirm", 0.15), "desc": f"подтверждение {confirm_norm:.0f}%"},
        {"name": "Риск-профиль", "value": risk_norm, "weight": weights.get("risk", 0.10), "desc": f"риск {risk_score}/10, RR 1:{rr:.1f}"},
        {"name": "Ранняя готовность импульса", "value": impulse_norm, "weight": weights.get("impulse", 0.10), "desc": f"импульс {impulse_norm:.0f}%"},
    ]

    # Weak spots
    weak = [b for b in breakdown if b["value"] < 55]
    weak_reasons = [f"{w['name']} {w['value']:.0f}% — {w['desc']}" for w in weak]

    return {
        "confidence": confidence,
        "label": label,
        "breakdown": breakdown,
        "weak_spots": weak_reasons,
        "components": {
            "quality": quality_norm,
            "data": data_norm,
            "trend": trend_norm,
            "confirm": confirm_norm,
            "risk": risk_norm,
            "impulse": impulse_norm,
        },
    }
