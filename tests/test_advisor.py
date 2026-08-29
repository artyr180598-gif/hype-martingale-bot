"""Тесты советника по сделке: риск-движок и карточка для новичка."""

import pytest

from src.analysis.advisor import BEGINNER_GUIDE, RiskEngine, TradeAdvisor
from src.analysis.engine import AnalysisEngine
from src.data.demo import DemoMarketSource

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "PEPEUSDT", "NOVAUSDT", "WIFUSDT", "JUPUSDT")


@pytest.fixture()
def engine(settings):
    return AnalysisEngine(DemoMarketSource(settings), settings)


@pytest.fixture()
def advisor(settings):
    return TradeAdvisor(DemoMarketSource(settings), settings)


async def _first_with_plan(engine, advisor, **kwargs):
    """Ищет монету с активным сценарием (LONG/SHORT) и возвращает карточку."""
    for sym in SYMBOLS:
        res = await engine.analyze(sym)
        card = await advisor.build(res, **kwargs)
        if card.side in ("LONG", "SHORT"):
            return card
    return None


# ── риск-движок ──
def test_position_size_matches_risk(settings):
    risk = RiskEngine(settings)
    qty, notional, margin, risk_usd, notes = risk.size_position(
        deposit=1000.0, risk_pct=1.0, entry=100.0, stop=98.0, leverage=5
    )
    # риск 1% от 1000 = 10 USD; стоп 2 USD на монету → 5 монет
    assert abs(qty - 5.0) < 1e-9
    assert abs(notional - 500.0) < 1e-9
    assert abs(margin - 100.0) < 1e-9
    assert abs(risk_usd - 10.0) < 1e-9


def test_position_size_respects_margin_cap(settings):
    risk = RiskEngine(settings)
    qty, notional, margin, risk_usd, notes = risk.size_position(
        deposit=1000.0, risk_pct=1.0, entry=100.0, stop=99.9, leverage=2
    )
    # стоп очень близко → позиция была бы огромной, маржа ограничена 15% депозита
    assert margin <= 1000.0 * settings.MAX_POSITION_PCT / 100.0 + 1e-6
    assert risk_usd < 10.0, "фактический риск должен быть урезан вместе с позицией"
    assert any("лимит" in n.lower() for n in notes)


def test_leverage_capped(settings):
    risk = RiskEngine(settings)
    assert risk.max_leverage(atr_pct=0.1, vol_state="low") <= min(settings.MAX_LEVERAGE, 10)
    assert risk.max_leverage(atr_pct=8.0, vol_state="extreme") >= 1
    assert risk.max_leverage(atr_pct=0.5, vol_state="normal", instrument_max=3) <= 3


def test_zero_stop_distance(settings):
    risk = RiskEngine(settings)
    qty, notional, margin, risk_usd, notes = risk.size_position(
        deposit=1000.0, risk_pct=1.0, entry=100.0, stop=100.0, leverage=5
    )
    assert qty == 0.0
    assert notes


# ── карточка сделки ──
@pytest.mark.asyncio
async def test_card_has_beginner_steps(engine, advisor):
    card = await _first_with_plan(engine, advisor, deposit_usd=500.0)
    if card is None:
        pytest.skip("на демо-рынке сейчас нет подтверждённого сценария")
    assert len(card.steps) >= 8, "пошаговая инструкция для новичка обязательна"
    text = card.to_text()
    assert "ЧТО НАЖИМАТЬ" in text
    assert "СКОЛЬКО ПОКУПАТЬ" in text
    assert "УРОВНИ" in text
    assert card.checklist and card.exit_rules


@pytest.mark.asyncio
async def test_card_money_math(engine, advisor):
    card = await _first_with_plan(engine, advisor, deposit_usd=1000.0, risk_pct=1.0)
    if card is None:
        pytest.skip("на демо-рынке сейчас нет подтверждённого сценария")
    # потеря на стопе не должна превышать заданный риск (с учётом округления вниз)
    assert card.qty > 0
    assert card.loss_stop_usd > 0
    assert card.loss_stop_usd <= 1000.0 * 0.01 * 1.35, "убыток на стопе сопоставим с риском 1%"
    assert card.notional_usd == pytest.approx(card.qty * card.entry_ref, rel=1e-6)
    if card.market == "futures":
        assert card.margin_usd == pytest.approx(card.notional_usd / card.leverage, rel=1e-6)
    assert card.leverage <= advisor.settings.MAX_LEVERAGE
    assert card.profit_t1_usd > card.loss_stop_usd, "при R:R > 1 прибыль больше убытка"


@pytest.mark.asyncio
async def test_card_levels_ordered(engine, advisor):
    card = await _first_with_plan(engine, advisor)
    if card is None:
        pytest.skip("на демо-рынке сейчас нет подтверждённого сценария")
    if card.side == "LONG":
        assert card.stop_loss < card.entry_zone[0]
        assert all(t > card.entry_zone[1] for t in card.targets)
    else:
        assert card.stop_loss > card.entry_zone[1]
        assert all(t < card.entry_zone[0] for t in card.targets)


@pytest.mark.asyncio
async def test_card_spot_forces_no_leverage(engine, advisor):
    card = await _first_with_plan(engine, advisor, market="spot", deposit_usd=500.0)
    if card is None:
        pytest.skip("на демо-рынке сейчас нет подтверждённого сценария")
    assert card.leverage == 1
    assert card.liq_price_est is None


@pytest.mark.asyncio
async def test_card_exchange_switch(engine, advisor):
    card = await _first_with_plan(engine, advisor, exchange="binance", deposit_usd=500.0)
    if card is None:
        pytest.skip("на демо-рынке сейчас нет подтверждённого сценария")
    assert card.exchange == "binance"
    assert any("Binance" in s for s in card.steps)


@pytest.mark.asyncio
async def test_card_serializable(engine, advisor):
    import json

    card = await _first_with_plan(engine, advisor, deposit_usd=250.0)
    if card is None:
        pytest.skip("на демо-рынке сейчас нет подтверждённого сценария")
    d = card.to_dict()
    json.dumps(d)
    assert d["symbol"] and "steps" in d


@pytest.mark.asyncio
async def test_wait_card_when_no_plan(settings):
    """WAIT — тоже полноценный ответ: объясняем, что делать."""
    src = DemoMarketSource(settings)
    engine = AnalysisEngine(src, settings)
    advisor = TradeAdvisor(src, settings)
    wait_card = None
    for sym in SYMBOLS:
        res = await engine.analyze(sym)
        card = await advisor.build(res, deposit_usd=500.0)
        if card.side == "WAIT":
            wait_card = card
            break
    if wait_card is None:
        pytest.skip("все монеты дали сценарий — WAIT не воспроизводится")
    assert wait_card.qty == 0.0
    assert wait_card.steps, "даже для WAIT нужна инструкция"
    assert not wait_card.risk_check.ok
    assert "WAIT" in wait_card.to_text() or "не входить" in wait_card.to_text()


def test_beginner_guide_content():
    assert "ПРАВИЛО РИСКА" in BEGINNER_GUIDE.upper()
    assert "Плечо" in BEGINNER_GUIDE
    assert "мартингейл" in BEGINNER_GUIDE.lower()
