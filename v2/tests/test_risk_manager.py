"""
Риск-менеджер: динамические уровни от ATR, размер позиции, вердикт.

Здесь проверяется самая важная математика бота — от неё зависят деньги.
Каждый тест фиксирует конкретное число, а не «значение похоже на правильное».
"""

from __future__ import annotations

import pytest

from v2.analysis.risk_manager import (
    build_plan,
    decide_verdict,
    dynamic_levels,
    position_size,
    recommended_leverage,
    risk_score,
)
from v2.models import (
    MicrostructureReport,
    SecurityReport,
    SocialReport,
    TechnicalReport,
    TradePlan,
    TrendSnapshot,
)


# ═══════════════════════════════════════════════════════════════
#  УРОВНИ ОТ ATR
# ═══════════════════════════════════════════════════════════════
def test_long_levels_are_atr_multiples(config):
    levels = dynamic_levels("LONG", entry=100.0, atr_value=2.0, config=config)
    assert levels.stop == pytest.approx(96.4)          # 100 − 1.8·2
    assert levels.targets[0] == pytest.approx(107.2)   # 100 + 3.6·2
    assert levels.rr == pytest.approx(2.0)             # 7.2 / 3.6
    assert levels.sl_pct == pytest.approx(3.6)


def test_short_levels_are_mirrored(config):
    levels = dynamic_levels("SHORT", entry=100.0, atr_value=2.0, config=config)
    assert levels.stop == pytest.approx(103.6)
    assert levels.targets[0] == pytest.approx(92.8)
    assert levels.rr == pytest.approx(2.0)


def test_structural_stop_replaces_atr_stop_when_reasonable(config):
    """Структурный уровень в 1 ATR важнее формулы — стоп ставится за ним."""
    levels = dynamic_levels("LONG", 100.0, 2.0, config, structure_stop=98.0)
    assert levels.stop == pytest.approx(98.0)
    assert levels.sl_multiplier == pytest.approx(1.0)
    assert levels.rr == pytest.approx(3.6)             # 7.2 / 2.0


def test_structural_stop_ignored_when_too_close(config):
    """Стоп ближе 0.8·ATR выбьет шумом — оставляем ATR-стоп."""
    levels = dynamic_levels("LONG", 100.0, 2.0, config, structure_stop=99.8)
    assert levels.stop == pytest.approx(96.4)


def test_structural_stop_ignored_when_too_far(config):
    """Стоп дальше 3.5·ATR делает позицию микроскопической — отбрасываем."""
    levels = dynamic_levels("LONG", 100.0, 2.0, config, structure_stop=88.0)
    assert levels.stop == pytest.approx(96.4)


def test_target_is_extended_to_meet_min_rr(config):
    """Если стоп широкий, цель уезжает дальше, чтобы сохранить R:R ≥ 2."""
    config.ATR_SL_MULTIPLIER = 3.0
    config.ATR_TP_MULTIPLIER = 3.0     # формально R:R было бы 1:1
    levels = dynamic_levels("LONG", 100.0, 2.0, config)
    assert levels.rr >= config.MIN_RISK_REWARD
    assert levels.targets[0] == pytest.approx(100 + 6.0 * 2.0)


def test_fib_targets_added_as_second_target(config):
    levels = dynamic_levels("LONG", 100.0, 2.0, config, fib_targets=[115.0, 120.0])
    assert len(levels.targets) >= 2
    assert levels.targets[0] == pytest.approx(107.2)
    assert 115.0 in [round(t, 2) for t in levels.targets]


def test_invalid_inputs_do_not_crash(config):
    assert dynamic_levels("LONG", 0.0, 2.0, config).direction == "WAIT"
    assert dynamic_levels("LONG", 100.0, 0.0, config).direction == "WAIT"
    assert dynamic_levels("SIDEWAYS", 100.0, 2.0, config).direction == "WAIT"


# ═══════════════════════════════════════════════════════════════
#  РАЗМЕР ПОЗИЦИИ
# ═══════════════════════════════════════════════════════════════
def test_position_size_from_risk_percent(config):
    qty, notional, risk, margin = position_size(
        deposit_usd=10_000, entry=100.0, stop=96.4, config=config, leverage=5
    )
    # риск 1% от 10k = $100, дистанция 3.6 → qty 27.78, объём $2777.8
    assert risk == pytest.approx(100.0)
    assert qty == pytest.approx(27.7777, abs=0.01)
    assert notional == pytest.approx(2777.77, abs=0.1)
    assert margin == pytest.approx(555.55, abs=0.1)


def test_position_capped_by_max_position_pct(config):
    """При депозите 1000 лимит 10% режет позицию, риск пересчитывается честно."""
    qty, notional, risk, _ = position_size(1000.0, 100.0, 96.4, config, leverage=1)
    assert notional == pytest.approx(100.0)     # 10% депозита при плече 1x
    assert qty == pytest.approx(1.0)
    assert risk == pytest.approx(3.6)           # 1.0 × 3.6, а не 10 (1%)


def test_position_size_zero_when_inputs_degenerate(config):
    assert position_size(1000.0, 0.0, 96.4, config) == (0.0, 0.0, 0.0, 0.0)
    assert position_size(1000.0, 100.0, 100.0, config) == (0.0, 0.0, 0.0, 0.0)
    assert position_size(0.0, 100.0, 96.4, config) == (0.0, 0.0, 0.0, 0.0)


def test_wider_stop_means_smaller_position(config):
    """Главное свойство ATR-подхода: риск в деньгах одинаков на любой монете."""
    _, _, risk_tight, _ = position_size(10_000, 100.0, 98.0, config, leverage=5)
    _, _, risk_wide, _ = position_size(10_000, 100.0, 90.0, config, leverage=5)
    assert risk_tight == pytest.approx(risk_wide) == pytest.approx(100.0)


def test_recommended_leverage_falls_with_volatility(config):
    assert recommended_leverage(0.5, config) == 4            # 2.0 / 0.5
    assert recommended_leverage(0.2, config) == config.MAX_LEVERAGE   # упрётся в потолок
    assert recommended_leverage(4.0, config) == 1
    assert recommended_leverage(0.0, config) >= 1


# ═══════════════════════════════════════════════════════════════
#  ОЦЕНКА РИСКА И ВЕРДИКТ
# ═══════════════════════════════════════════════════════════════
def _tech(adx_value=40.0, direction="up", atr_pct=1.0, atr=1.0) -> TechnicalReport:
    report = TechnicalReport(price=100.0, atr=atr, atr_pct=atr_pct)
    report.trend = TrendSnapshot(timeframe="1h", adx=adx_value, plus_di=30, minus_di=10,
                                 direction=direction, strength="moderate")
    return report


def _micro(grade="ok", slippage=0.2) -> MicrostructureReport:
    return MicrostructureReport(entry_size_usd=5000, grade=grade, slippage_pct=slippage, mid_price=100.0)


def test_risk_score_is_low_for_clean_token():
    security = SecurityReport(score=95.0)
    risk, why = risk_score(security, _tech(), _micro("excellent", 0.05), SocialReport(hype_score=30))
    assert risk <= 3
    assert why  # каждое слагаемое объяснено


def test_risk_score_maxed_when_security_blocked():
    security = SecurityReport(score=10.0, blocked=True, blockers=["mint()"])
    risk, _ = risk_score(security, _tech(), _micro("thin", 1.5), SocialReport(hype_score=80))
    assert risk >= 9


def test_risk_score_grows_with_volatility_and_thin_book():
    base = risk_score(SecurityReport(score=80), _tech(atr_pct=1.0), _micro("excellent", 0.05), SocialReport())[0]
    volatile = risk_score(SecurityReport(score=80), _tech(atr_pct=12.0), _micro("thin", 1.8), SocialReport())[0]
    assert volatile > base


def test_verdict_avoid_when_blocked(config):
    security = SecurityReport(score=5.0, blocked=True, blockers=["⛔ mint()"])
    verdict, _, risks = decide_verdict(security, _tech(), _micro(), TradePlan(direction="LONG", rr=3.0), 3, config)
    assert verdict == "AVOID"
    assert risks


def test_verdict_avoid_when_book_empty(config):
    verdict, _, _ = decide_verdict(SecurityReport(score=90), _tech(), _micro("empty", 5.0),
                                   TradePlan(direction="LONG", rr=3.0), 2, config)
    assert verdict == "AVOID"


def test_verdict_watch_when_rr_below_minimum(config):
    verdict, _, risks = decide_verdict(SecurityReport(score=90), _tech(), _micro(),
                                       TradePlan(direction="LONG", rr=1.2), 2, config)
    assert verdict == "WATCH"
    assert any("R:R" in r for r in risks)


def test_verdict_watch_when_no_direction(config):
    verdict, _, _ = decide_verdict(SecurityReport(score=90), _tech(), _micro(),
                                   TradePlan(direction="WAIT"), 2, config)
    assert verdict == "WATCH"


def test_verdict_enter_on_clean_setup(config):
    security = SecurityReport(score=92.0, passed=["✅ LP заблокирована"])
    plan = TradePlan(direction="LONG", entry=100.0, stop_loss=96.4, targets=[107.2], rr=2.0,
                     risk_usd=10.0, qty=2.7)
    verdict, reasons, _ = decide_verdict(security, _tech(), _micro(), plan, 2, config)
    assert verdict == "ENTER"
    assert reasons


def test_verdict_watch_when_risk_too_high(config):
    plan = TradePlan(direction="LONG", entry=100.0, stop_loss=96.4, targets=[107.2], rr=2.0, qty=1.0)
    verdict, _, _ = decide_verdict(SecurityReport(score=60), _tech(atr_pct=11.0), _micro("thin", 1.9),
                                   plan, 7, config)
    assert verdict == "WATCH"


# ═══════════════════════════════════════════════════════════════
#  СБОРКА ПЛАНА
# ═══════════════════════════════════════════════════════════════
def test_build_plan_produces_consistent_numbers(config):
    plan = build_plan("LONG", 100.0, _tech(atr=2.0, atr_pct=2.0), _micro(), config,
                      deposit_usd=10_000, risk=3)
    assert plan.direction == "LONG"
    assert plan.stop_loss == pytest.approx(96.4)
    assert plan.rr >= config.MIN_RISK_REWARD
    assert plan.risk_usd > 0
    assert plan.trailing_stop > plan.entry          # трейлинг выше входа для лонга
    assert "1h" in plan.invalidation


def test_build_plan_reduces_size_for_high_risk(config):
    safe = build_plan("LONG", 100.0, _tech(atr=2.0), _micro(), config, deposit_usd=10_000, risk=1)
    risky = build_plan("LONG", 100.0, _tech(atr=2.0), _micro(), config, deposit_usd=10_000, risk=8)
    assert risky.position_usd < safe.position_usd


def test_build_plan_wait_without_atr(config):
    plan = build_plan("LONG", 100.0, _tech(atr=0.0), _micro(), config, deposit_usd=1000, risk=3)
    assert plan.direction == "WAIT"
    assert plan.why
