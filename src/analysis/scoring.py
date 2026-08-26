"""
Многокритериальный скоринг монеты (0–100) для поиска «скрытых» возможностей.

Критерии подобраны под задачу: находить монеты с живым объёмом, умеренной
капитализацией, активизацией волатильности и бычьим моментумом — то, что
не входит в топ-10 по капитализации, но начинает движение.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ScoreBreakdown:
    total: float
    parts: dict[str, float] = field(default_factory=dict)


def _clip01(x: float) -> float:
    return float(np.clip(x, 0.0, 1.0))


def score_hidden_gem(
    price_24h_pct: float,
    turnover_usd: float,
    volume_z: float,
    atr_pctl: float,
    rsi: float,
    roc_20: float,
    market_cap: float | None,
    st_dir: int,
    squeeze: bool,
    funding_rate: float | None,
    is_major: bool,
    liquidity_ok: bool = True,
) -> ScoreBreakdown:
    """Оценка «скрытой» монеты. Каждый блок даёт до N баллов (сумма ≤ 100)."""
    parts: dict[str, float] = {}

    # 1. Активизация волатильности (0–25): ATR-процентиль + объём z-score
    parts["volatility"] = 25.0 * (0.6 * _clip01(atr_pctl) + 0.4 * _clip01((volume_z + 1) / 3))

    # 2. Моментум (0–25): ROC20 + направление supertrend + RSI-оптимум
    roc_score = _clip01((roc_20 + 10) / 35)
    rsi_opt = _clip01(1.0 - abs(rsi - 62) / 45)  # бычий оптимум ~62, не перекуплено
    parts["momentum"] = 25.0 * (0.45 * roc_score + 0.3 * max(0.0, st_dir) * rsi_opt + 0.25 * rsi_opt)

    # 3. Приток денег / объём (0–20): оборот в логарифмической шкале
    log_turn = np.log10(max(turnover_usd, 1.0))
    turnover_score = _clip01((log_turn - 6.0) / 4.5)  # 1M→0, 30M+→1
    parts["volume"] = 20.0 * turnover_score if liquidity_ok else 20.0 * turnover_score * 0.3

    # 4. «Скрытность» (0–15): не топ-монета + умеренная капитализация
    if market_cap and market_cap > 0:
        log_cap = np.log10(market_cap)
        cap_score = _clip01(1.0 - abs(log_cap - 8.6) / 3.0)  # оптимум ~$400M
    else:
        cap_score = 0.6
    hidden = 1.0 if not is_major else 0.25
    parts["hidden"] = 15.0 * (0.6 * hidden + 0.4 * cap_score)

    # 5. Сжатие перед движением (0–15): squeeze — бонус, экстремальный ATR — штраф
    if squeeze:
        squeeze_score = 1.0
    elif atr_pctl >= 0.9:
        squeeze_score = 0.1  # уже разогналось — входить поздно
    elif atr_pctl >= 0.75:
        squeeze_score = 0.45
    else:
        squeeze_score = 0.7
    parts["setup"] = 15.0 * squeeze_score

    # 6. Деривативы (0–5): нейтральный/положительный фандинг без перегрева
    if funding_rate is None:
        fund_score = 0.5
    else:
        fund = max(-0.001, min(0.002, funding_rate))
        fund_score = _clip01(1.0 - abs(fund) / 0.002)
    parts["derivatives"] = 5.0 * fund_score

    total = float(sum(parts.values()))
    return ScoreBreakdown(total=round(min(100.0, total), 1), parts={k: round(v, 1) for k, v in parts.items()})


def tier_from_score(score: float) -> str:
    if score >= 80:
        return "A+"
    if score >= 68:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def verdict_from_score(score: float) -> tuple[str, str]:
    """(действие, рекомендация) по итоговому баллу."""
    if score >= 80:
        return "STRONG_BUY", "Сильный сигнал: совокупность факторов редкая"
    if score >= 68:
        return "BUY", "Бычий набор факторов"
    if score >= 55:
        return "WATCH", "Интересно, но лучше дождаться подтверждения"
    if score >= 40:
        return "NEUTRAL", "Нейтрально: искать точки по уровням"
    return "AVOID", "Слабый набор факторов — не входить"
