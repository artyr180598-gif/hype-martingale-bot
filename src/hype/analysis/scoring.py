"""Quality scoring — grades setup S/A/B/C."""

from __future__ import annotations

import pandas as pd

from ..config import Settings


def calculate_quality_score(
    confluence: dict,
    regime: dict,
    orderbook_analysis: dict,
    risk_plan,
    signals: list[dict],
    early_impulse: dict,
    cfg: Settings,
) -> dict:
    """
    Quality score 0-100, grade S/A/B/C.
    Combines confluence, regime, orderbook, risk, signals, impulse.
    """
    score = 50.0
    reasons = []

    # Confluence bias margin
    try:
        bias_margin = confluence.get("bias_margin", 0)
        if bias_margin > 30:
            score += 15
            reasons.append(f"Сильный консенсус {bias_margin:.0f}% — 11 стратегий согласны")
        elif bias_margin > 15:
            score += 8
            reasons.append(f"Умеренный консенсус {bias_margin:.0f}%")
        elif bias_margin < 5:
            score -= 10
            reasons.append(f"Слабый консенсус {bias_margin:.0f}% — конфликт стратегий")
    except Exception:
        pass

    # Regime
    try:
        regime_name = regime.get("regime", "UNCERTAIN")
        if regime_name in ("TRENDING_UP", "TRENDING_DOWN", "BREAKOUT", "BREAKDOWN"):
            score += 10
            reasons.append(f"Режим {regime_name} — тренд подтвержден")
        elif regime_name in ("RANGING", "ACCUMULATION"):
            score += 3
            reasons.append(f"Режим {regime_name} — боковик, нужен пробой")
        elif regime_name == "HIGH_VOLATILITY":
            score -= 5
            reasons.append("Высокая волатильность — риск проскальзывания")
        elif regime_name == "UNCERTAIN":
            score -= 8
            reasons.append("Неопределенный режим")
    except Exception:
        pass

    # Orderbook
    try:
        imbalance = orderbook_analysis.get("imbalance", 0.5)
        liq_score = orderbook_analysis.get("liquidity_score", 50)
        if liq_score > 70:
            score += 5
            reasons.append(f"Плотный стакан {liq_score:.0f}% — ликвидность есть")
        elif liq_score < 30:
            score -= 7
            reasons.append(f"Тонкий стакан {liq_score:.0f}% — риск проскальзывания")

        # Imbalance in favor
        # We need direction; assume we check later, here generic
        if imbalance > 0.65 or imbalance < 0.35:
            score += 5
            reasons.append(f"Дисбаланс стакана {imbalance:.2f} — давление")
    except Exception:
        pass

    # Risk / RR
    try:
        rr = risk_plan.risk_reward if risk_plan else 0
        if rr >= 3.0:
            score += 12
            reasons.append(f"Отличный R:R 1:{rr:.1f}")
        elif rr >= 2.0:
            score += 7
            reasons.append(f"Хороший R:R 1:{rr:.1f}")
        elif rr < 1.5:
            score -= 10
            reasons.append(f"Слабый R:R 1:{rr:.1f}")
    except Exception:
        pass

    # Signals STOBB/SBM/JUMP
    try:
        has_sbm = any(s["type"] == "SBM" for s in signals)
        has_stobb = any(s["type"] == "STOBB" for s in signals)
        has_jump = any(s["type"] == "JUMP" for s in signals)
        if has_sbm:
            score += 15
            reasons.append("SBM сигнал — STOBB + MA alignment + PSAR")
        elif has_stobb:
            score += 10
            reasons.append("STOBB сигнал — перепроданность + BB")
        if has_jump:
            score += 8
            reasons.append("JUMP — резкий импульс объема/цены")
    except Exception:
        pass

    # Early impulse
    try:
        phase = early_impulse.get("phase", "WATCH")
        heat = early_impulse.get("heat", 0)
        if phase == "TRIGGERED":
            score += 10
            reasons.append(f"⚡ Импульс TRIGGERED heat {heat:.0f} — пробой подтвержден")
        elif phase == "EARLY":
            score += 6
            reasons.append(f"🌱 Импульс EARLY heat {heat:.0f} — база просыпается")
        elif phase == "EXHAUSTED":
            score -= 15
            reasons.append(f"💨 Импульс EXHAUSTED — движение выжато, не догоняем")
    except Exception:
        pass

    # Clamp
    score = max(0, min(100, score))

    # Grade
    if score >= 85:
        grade = "S"
    elif score >= 75:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 45:
        grade = "C"
    else:
        grade = "D"

    return {"score": score, "grade": grade, "reasons": reasons}
