"""
Риск-менеджер v2: динамические стоп/цель от ATR + размер позиции + вердикт.

Почему ATR, а не «стоп 2%»:
  фиксированный процент не знает, насколько монета шумная. У BTC ATR ≈ 1% —
  стоп в 2% выживет; у свежего мемкоина ATR ≈ 9% — тот же стоп в 2% выбьет
  первым же тиком. Привязка к ATR делает риск одинаковым в единицах
  волатильности на любой монете, а размер позиции подгоняется под стоп, поэтому
  потеря в деньгах остаётся равной RISK_PER_TRADE_PCT от депозита.

Порядок расчёта (жёсткий, как в nautilus_trader):
  1. стоп  = вход ∓ clamp(ATR_SL_MULTIPLIER)·ATR  (или структурный уровень);
  2. цель  = вход ± ATR_TP_MULTIPLIER·ATR, но не ближе MIN_RISK_REWARD·риск;
  3. объём = (депозит × риск%) / |вход − стоп|, с обрезкой по MAX_POSITION_PCT;
  4. проверка: R:R ≥ MIN_RISK_REWARD, иначе сделка не предлагается вовсе.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from v2.config import V2Config
from v2.core.logging import get_logger
from v2.models import (
    CoinReport,
    MicrostructureReport,
    SecurityReport,
    SocialReport,
    TechnicalReport,
    TradePlan,
    Verdict,
)

logger = get_logger("analysis.risk")


# ═══════════════════════════════════════════════════════════════
#  УРОВНИ ОТ ATR
# ═══════════════════════════════════════════════════════════════
@dataclass
class LevelSet:
    direction: str
    entry: float
    stop: float
    targets: list[float] = field(default_factory=list)
    atr: float = 0.0
    sl_multiplier: float = 0.0
    sl_pct: float = 0.0
    rr: float = 0.0
    why: list[str] = field(default_factory=list)


def dynamic_levels(
    direction: str,
    entry: float,
    atr_value: float,
    config: V2Config,
    *,
    structure_stop: float | None = None,
    fib_targets: list[float] | None = None,
) -> LevelSet:
    """
    Считает стоп и цели.

    structure_stop — уровень от структуры рынка (свинг/фибо). Используется,
    если он не уводит риск дальше ATR_MAX_SL_MULTIPLIER·ATR: структура важнее
    формулы, но без фанатизма — иначе стоп улетает на 15% и позиция становится
    микроскопической.
    """
    if entry <= 0 or atr_value <= 0 or direction not in ("LONG", "SHORT"):
        return LevelSet(direction="WAIT", entry=entry, stop=0.0)

    is_long = direction == "LONG"
    sl_mult = min(max(config.ATR_SL_MULTIPLIER, config.ATR_MIN_SL_MULTIPLIER), config.ATR_MAX_SL_MULTIPLIER)

    atr_stop = entry - sl_mult * atr_value if is_long else entry + sl_mult * atr_value
    stop = atr_stop
    why = [f"ATR = {atr_value:.8g} ({atr_value / entry * 100:.2f}% цены), стоп = {sl_mult:.1f}·ATR от входа"]

    # структурный стоп принимаем, только если он «близко» и защищает сделку
    if structure_stop is not None and structure_stop > 0:
        distance = abs(entry - structure_stop)
        if distance <= config.ATR_MAX_SL_MULTIPLIER * atr_value and (
            (is_long and structure_stop < entry) or (not is_long and structure_stop > entry)
        ):
            if distance >= config.ATR_MIN_SL_MULTIPLIER * atr_value:
                stop = structure_stop
                sl_mult = distance / atr_value
                why.append(
                    f"стоп перенесён на структурный уровень {structure_stop:.8g} "
                    f"(это {sl_mult:.2f}·ATR) — за ним сделка теряет смысл"
                )
            else:
                why.append(
                    f"структурный уровень {structure_stop:.8g} слишком близко "
                    f"(< {config.ATR_MIN_SL_MULTIPLIER}·ATR) — шум выбьет стоп, оставили ATR-стоп"
                )

    risk = abs(entry - stop)
    sl_pct = risk / entry * 100.0

    # ── цели ─────────────────────────────────────────────────────
    tp_mult = max(config.ATR_TP_MULTIPLIER, config.MIN_RISK_REWARD * sl_mult)
    base_target = entry + tp_mult * atr_value if is_long else entry - tp_mult * atr_value
    targets = [base_target]

    # вторая/третья цели — из фибо, если они дальше первой
    for fib_target in fib_targets or []:
        if fib_target <= 0:
            continue
        better = fib_target > base_target if is_long else fib_target < base_target
        if better and all(abs(fib_target - t) / entry > 0.005 for t in targets):
            targets.append(fib_target)
    targets = sorted(targets, reverse=not is_long)[:3]

    from v2.analysis.indicators import compute_rr

    rr = compute_rr(entry, stop, targets[0])
    why.append(
        f"цель 1 = {targets[0]:.8g} → {tp_mult:.1f}·ATR, "
        f"соотношение риск/прибыль 1:{rr:.1f}"
    )
    return LevelSet(
        direction=direction,
        entry=entry,
        stop=stop,
        targets=targets,
        atr=atr_value,
        sl_multiplier=round(sl_mult, 2),
        sl_pct=round(sl_pct, 3),
        rr=round(rr, 2),
        why=why,
    )


# ═══════════════════════════════════════════════════════════════
#  РАЗМЕР ПОЗИЦИИ
# ═══════════════════════════════════════════════════════════════
def position_size(
    deposit_usd: float,
    entry: float,
    stop: float,
    config: V2Config,
    *,
    risk_pct: float | None = None,
    leverage: int | None = None,
) -> tuple[float, float, float, float]:
    """
    Возвращает (qty, notional_usd, risk_usd, margin_usd).

    Риск в деньгах фиксирован процентом депозита; объём — производная от
    расстояния до стопа. Чем шире стоп (волатильная монета), тем меньше позиция:
    именно это удерживает потерю в рамках 1% депозита на любом токене.
    """
    risk_pct = config.RISK_PER_TRADE_PCT if risk_pct is None else risk_pct
    leverage = max(1, min(config.MAX_LEVERAGE, leverage or 1))
    distance = abs(entry - stop)
    if deposit_usd <= 0 or entry <= 0 or distance <= 0:
        return 0.0, 0.0, 0.0, 0.0

    risk_usd = deposit_usd * risk_pct / 100.0
    qty = risk_usd / distance
    notional = qty * entry

    # потолок: маржа не больше MAX_POSITION_PCT% депозита
    max_notional = deposit_usd * config.MAX_POSITION_PCT / 100.0 * leverage
    if notional > max_notional:
        notional = max_notional
        qty = notional / entry
        risk_usd = qty * distance  # честный пересчёт риска после обрезки
    margin = notional / leverage
    return qty, notional, risk_usd, margin


def recommended_leverage(atr_pct: float, config: V2Config) -> int:
    """Плечо от волатильности:目标 риск на плечо ≈ 2% движения цены."""
    if atr_pct <= 0:
        return max(1, min(config.MAX_LEVERAGE, 3))
    raw = int(2.0 / max(atr_pct, 0.2))
    return max(1, min(config.MAX_LEVERAGE, raw))


# ═══════════════════════════════════════════════════════════════
#  ОЦЕНКА РИСКА 1..10 И ВЕРДИКТ
# ═══════════════════════════════════════════════════════════════
def risk_score(
    security: SecurityReport,
    technical: TechnicalReport,
    micro: MicrostructureReport,
    social: SocialReport,
) -> tuple[int, list[str]]:
    """
    Риск 1..10 (10 — максимальный). Собирается из четырёх блоков, каждый
    добавляет баллы с понятным пояснением — они попадают в отчёт.
    """
    score = 1.0
    why: list[str] = []

    # 1. Безопасность контракта/пула — самый тяжёлый блок (до +4)
    sec = security.score  # 0..100, больше = безопаснее
    sec_risk = (100.0 - sec) / 100.0 * 4.0
    score += sec_risk
    why.append(f"безопасность контракта/пула: {sec:.0f}/100 → +{sec_risk:.1f} к риску")
    if security.blocked:
        why.append("есть блокирующие признаки скама (см. раздел Безопасность)")
        score = max(score, 9.0)

    # 2. Ликвидность и проскальзывание (до +2.5)
    if micro.grade == "empty":
        score += 2.5
        why.append("стакан/ликвидность не позволяют войти объёмом $5k без потерь → +2.5")
    elif micro.grade == "thin":
        score += 1.5
        why.append(f"тонкая ликвидность: проскальзывание {micro.slippage_pct:.2f}% → +1.5")
    elif micro.grade == "ok":
        score += 0.7
        why.append(f"ликвидность приемлемая: проскальзывание {micro.slippage_pct:.2f}% → +0.7")
    else:
        why.append(f"ликвидность хорошая: проскальзывание {micro.slippage_pct:.2f}% → +0.0")

    # 3. Волатильность (до +2)
    atr_pct = technical.atr_pct
    if atr_pct >= 10:
        score += 2.0
        why.append(f"ATR {atr_pct:.1f}% от цены — экстремальная волатильность → +2.0")
    elif atr_pct >= 5:
        score += 1.2
        why.append(f"ATR {atr_pct:.1f}% от цены — высокая волатильность → +1.2")
    elif atr_pct >= 2:
        score += 0.5
        why.append(f"ATR {atr_pct:.1f}% от цены — нормальная волатильность → +0.5")
    else:
        why.append(f"ATR {atr_pct:.1f}% от цены — низкая волатильность → +0.0")

    # 4. Рыночный контекст: тренд против + хайп без подтверждения (до +1.5)
    if technical.trend.direction == "down" and technical.trend.adx >= 25:
        score += 1.0
        why.append(f"тренд {technical.trend.timeframe} нисходящий (ADX {technical.trend.adx:.0f}) → +1.0")
    if social.hype_score >= 70 and not technical.accumulation.accumulation:
        score += 0.8
        why.append(
            f"хайп {social.hype_score:.0f}/100 без накопления объёмом → +0.8 (риск купить на пике)"
        )
    if not social.is_stub and social.sentiment < -0.3:
        score += 0.7
        why.append(f"негативный соцфон (сентимент {social.sentiment:+.2f}) → +0.7")

    # Неполнота данных — тоже риск
    degraded_count = len(security.degraded) + len(technical.degraded)
    if degraded_count:
        score += min(1.0, 0.25 * degraded_count)
        why.append(f"не получено данных по {degraded_count} проверкам → +{min(1.0, 0.25 * degraded_count):.1f}")

    return int(round(min(10.0, max(1.0, score)))), why


def decide_verdict(
    security: SecurityReport,
    technical: TechnicalReport,
    micro: MicrostructureReport,
    plan: TradePlan,
    risk: int,
    config: V2Config,
) -> tuple[Verdict, list[str], list[str]]:
    """
    Вердикт: Входить / Наблюдать / Не входить.

    Правила приоритетны: сначала блокирующие (скам, пустой стакан), потом
    качество сетапа (R:R, тренд), и только затем — разрешение на вход.
    """
    reasons: list[str] = []
    risks: list[str] = []

    if security.blocked:
        for blocker in security.blockers[:4]:
            risks.append(blocker)
        return "AVOID", reasons, risks

    if risk >= config.MAX_RISK_SCORE_TO_ENTER + 2:
        risks.append(f"Интегральный риск {risk}/10 — слишком высоко для входа")
        return "AVOID", reasons, risks

    if micro.grade == "empty":
        risks.append("Войти объёмом $5k невозможно без проскальзывания >2% — ликвидности нет")
        return "AVOID", reasons, risks

    if plan.direction == "WAIT":
        reasons.append("Нет направленного сигнала: тренд не подтверждён, вход не предлагается")
        return "WATCH", reasons, risks

    if plan.rr < config.MIN_RISK_REWARD:
        risks.append(
            f"R:R 1:{plan.rr:.1f} ниже требуемого 1:{config.MIN_RISK_REWARD:.1f} — "
            "математика сделки отрицательная"
        )
        return "WATCH", reasons, risks

    if risk > config.MAX_RISK_SCORE_TO_ENTER:
        risks.append(f"Риск {risk}/10 выше порога {config.MAX_RISK_SCORE_TO_ENTER} — только наблюдение")
        return "WATCH", reasons, risks

    if security.score < 55:
        risks.append(f"Оценка безопасности {security.score:.0f}/100 — заходите только объёмом ниже расчётного")

    # Положительные основания
    if technical.trend.direction == "up":
        reasons.append(
            f"Тренд {technical.trend.timeframe} восходящий (ADX {technical.trend.adx:.0f}) — торгуем по тренду"
        )
    if technical.accumulation.accumulation:
        reasons.append(
            f"Накопление на {technical.accumulation.timeframe}: OBV растёт быстрее цены"
        )
    if security.score >= 75:
        reasons.append(f"Безопасность {security.score:.0f}/100: LP заблокирована, опасных функций нет")
    if micro.grade in ("excellent", "ok"):
        reasons.append(
            f"Ликвидность позволяет войти на ${micro.entry_size_usd:,.0f} "
            f"с проскальзыванием {micro.slippage_pct:.2f}%"
        )
    if plan.rr >= config.MIN_RISK_REWARD and plan.targets:
        profit_usd = abs(plan.targets[0] - plan.entry) * plan.qty
        reasons.append(
            f"R:R 1:{plan.rr:.1f}: при стопе теряете ${plan.risk_usd:,.2f}, "
            f"по цели 1 зарабатываете ${profit_usd:,.2f}"
        )
    if not reasons:
        reasons.append("Формальные критерии входа выполнены, сильных подтверждений нет")
    return "ENTER", reasons, risks


def build_plan(
    direction: str,
    entry: float,
    technical: TechnicalReport,
    micro: MicrostructureReport,
    config: V2Config,
    *,
    deposit_usd: float,
    risk: int,
    structure_stop: float | None = None,
) -> TradePlan:
    """Собирает TradePlan: уровни от ATR + объём от риска."""
    if direction not in ("LONG", "SHORT") or entry <= 0 or technical.atr <= 0:
        return TradePlan(direction="WAIT", entry=entry, why=["Недостаточно данных для расчёта уровней"])

    fib_targets: list[float] = []
    if direction == "LONG":
        fib_targets = [
            technical.fib.extensions.get("1.272", 0.0),
            technical.fib.extensions.get("1.618", 0.0),
        ]
    else:
        fib_targets = [
            technical.fib.extensions.get("1.272", 0.0),
            technical.fib.extensions.get("1.618", 0.0),
        ]

    levels = dynamic_levels(
        direction,
        entry,
        technical.atr,
        config,
        structure_stop=structure_stop,
        fib_targets=[t for t in fib_targets if t > 0],
    )

    # Риск-скор корректирует размер позиции: чем опаснее монета, тем меньше вход
    risk_pct = config.RISK_PER_TRADE_PCT
    if risk >= 7:
        risk_pct *= 0.35
    elif risk >= 5:
        risk_pct *= 0.6
    elif risk <= 2:
        risk_pct *= 1.2
    risk_pct = min(risk_pct, config.RISK_PER_TRADE_PCT * 1.5)

    leverage = recommended_leverage(technical.atr_pct, config)
    qty, notional, risk_usd, margin = position_size(
        deposit_usd, entry, levels.stop, config, risk_pct=risk_pct, leverage=leverage
    )

    position_pct = (notional / deposit_usd * 100.0) if deposit_usd > 0 else 0.0
    trailing = (
        entry + config.TRAILING_ATR_MULTIPLIER * technical.atr
        if direction == "LONG"
        else entry - config.TRAILING_ATR_MULTIPLIER * technical.atr
    )

    why = list(levels.why)
    wanted_risk = deposit_usd * risk_pct / 100.0
    if risk_usd < wanted_risk * 0.999:
        why.append(
            f"риск задан {risk_pct:.2f}% депозита (${wanted_risk:,.2f}), но позиция урезана лимитом "
            f"{config.MAX_POSITION_PCT:.0f}% депозита — фактический риск ${risk_usd:,.2f} "
            f"({risk_usd / deposit_usd * 100 if deposit_usd else 0:.2f}%)"
        )
    else:
        why.append(
            f"риск на сделку {risk_pct:.2f}% депозита (${risk_usd:,.2f}) скорректирован по риску {risk}/10"
        )
    why.append(
        f"объём {qty:,.6f} шт. ≈ ${notional:,.2f} ({position_pct:.2f}% депозита), "
        f"маржа ${margin:,.2f} при плече {leverage}x"
    )
    why.append(
        f"после достижения цели 1 стоп переносится в безубыток, трейлинг — {trailing:.8g} "
        f"({config.TRAILING_ATR_MULTIPLIER}·ATR)"
    )

    invalidation = (
        f"закрытие {technical.trend.timeframe}-свечи ниже {levels.stop:.8g}"
        if direction == "LONG"
        else f"закрытие {technical.trend.timeframe}-свечи выше {levels.stop:.8g}"
    )

    return TradePlan(
        direction=direction,
        entry=round(entry, 8),
        stop_loss=round(levels.stop, 8),
        targets=[round(t, 8) for t in levels.targets],
        atr=round(technical.atr, 8),
        atr_sl_pct=round(levels.sl_pct, 3),
        rr=levels.rr,
        position_pct=round(position_pct, 2),
        position_usd=round(notional, 2),
        qty=round(qty, 8),
        risk_usd=round(risk_usd, 2),
        leverage=leverage,
        trailing_stop=round(trailing, 8),
        invalidation=invalidation,
        why=why,
    )


def direction_from_technical(technical: TechnicalReport, *, has_cex_listing: bool = False) -> str:
    """
    Направление сделки.

    Шорт предлагается только для токенов с листингом на CEX (там есть чем
    шортить); DEX-монету с заблокированной LP шортить негде, поэтому для неё
    возможен только LONG или WAIT.
    """
    if technical.trend.direction == "up" and technical.trend.adx >= 20:
        return "LONG"
    if technical.accumulation.accumulation and technical.trend.direction != "down":
        return "LONG"
    if has_cex_listing and technical.trend.direction == "down" and technical.trend.adx >= 30:
        return "SHORT"
    return "WAIT"


def assemble_coin_report(
    token,
    security: SecurityReport,
    technical: TechnicalReport,
    micro: MicrostructureReport,
    social: SocialReport,
    config: V2Config,
    *,
    deposit_usd: float,
) -> CoinReport:
    """Финальная сборка: риск → вердикт → план → отчёт."""
    risk, risk_why = risk_score(security, technical, micro, social)

    direction = direction_from_technical(technical, has_cex_listing=bool(token.cex_symbol))

    entry_price = micro.est_fill_price if micro.est_fill_price > 0 else (technical.price or token.price_usd)
    structure_stop = technical.fib.retracements.get("0.786") if direction == "LONG" else None

    plan = build_plan(
        direction,
        entry_price,
        technical,
        micro,
        config,
        deposit_usd=deposit_usd,
        risk=risk,
        structure_stop=structure_stop,
    )
    verdict, reasons, risks = decide_verdict(security, technical, micro, plan, risk, config)

    # интегральная привлекательность
    score = (
        0.45 * security.score
        + 0.30 * technical.score
        + 0.15 * (100.0 if micro.grade == "excellent" else 75.0 if micro.grade == "ok" else 35.0 if micro.grade == "thin" else 5.0)
        + 0.10 * min(100.0, social.hype_score)
    )
    if plan.direction == "WAIT":
        score *= 0.85

    confidence = _confidence(security, technical, micro, social)

    report = CoinReport(
        token=token,
        security=security,
        micro=micro,
        technical=technical,
        social=social,
        plan=plan,
        verdict=verdict,
        risk_score=risk,
        confidence=confidence,
        score=score,
        reasons=reasons,
        risks=risks,
    )
    report.reasons.extend(risk_why[:3])
    report.summary = _summary(report)
    report.degraded = sorted({*security.degraded, *technical.degraded})
    return report


def _confidence(security, technical, micro, social) -> float:
    """0..1 — насколько отчёт опирается на полные данные (честность важнее красоты)."""
    total = 4
    ok = 0
    if not security.degraded and not security.blocked:
        ok += 1
    elif not security.degraded:
        ok += 0.5
    if not technical.degraded:
        ok += 1
    if micro.grade != "empty":
        ok += 1
    if not social.is_stub:
        ok += 1
    return round(ok / total, 2)


def _summary(report: CoinReport) -> str:
    token = report.token
    return (
        f"{token.symbol} ({token.chain}): цена ${token.price_usd:.8g}, "
        f"риск {report.risk_score}/10, уверенность в данных {report.confidence * 100:.0f}%"
    )
