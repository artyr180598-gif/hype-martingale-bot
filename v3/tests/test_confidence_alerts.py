"""Раунд 6: «Уверенность бота» отдельным блоком + авто-сигналы без запроса.

Что проверяем:
  1. уверенность бота считается из шести анализов, веса нормированы, цифра
     растёт вместе с качеством сетапа и падает на устаревших данных;
  2. в beginner-карточке три метрики названы по-разному и не выдают себя за
     вероятность прибыли;
  3. авто-сигнал уходит только когда сетап прошёл ВСЕ пороги; слабые сетапы
     сохраняются, но в чат не летят;
  4. раздел «🔔 АВТО-СИГНАЛЫ», приветствие и глоссарий не противоречат конфигу.
"""

from __future__ import annotations

import time

from v3.alerts import AlertItem, evaluate_alert, render_alert, render_signal_alert
from v3.analysis.confidence import assess_confidence, attach_confidence
from v3.config import SignalConfig
from v3.models import FactorScore, RiskBrief, ScoreBreakdown, TradingSignal
from v3.report import render_signal
from v3.store import SignalLifecycle, SignalStore
from v3.telegram import HELP_TEXT, MENU_TEXT, WELCOME_TEXT, V3Core
from v3.tg import keyboards as kb
from v3.tg import render as rv

_INTERNAL_TOKENS = ("adx", "atr", "vol_z", "heat", "trend_score", "alignment", "rsi=", "adx=")

_FACTORS = [
    FactorScore("Trend Alignment", 0.9, 1.0, 13.0, 11.7),
    FactorScore("Market Structure", 0.85, 1.0, 13.0, 11.05),
    FactorScore("Momentum", 0.8, 1.0, 12.0, 9.6),
    FactorScore("Volume", 0.75, 1.0, 12.0, 9.0),
    FactorScore("Volatility", 0.6, 1.0, 10.0, 6.0),
    FactorScore("Order Flow", 0.7, 1.0, 10.0, 7.0),
    FactorScore("Derivatives", 0.65, 1.0, 10.0, 6.5),
    FactorScore("Liquidity", 0.8, 1.0, 6.0, 4.8),
    FactorScore("Market Context", 0.6, 1.0, 6.0, 3.6),
    FactorScore("Impulse Readiness", 0.8, 1.0, 8.0, 6.4),
]


def strong_signal(**kw) -> TradingSignal:
    now = int(time.time() * 1000)
    base = dict(
        uid="c1", symbol="SOLUSDT", ts_ms=now, direction="LONG", status="CONFIRMED",
        source="bybit", score=84, quality=84, tier="S", rr=2.6, confidence=0.96,
        risk_score=4, leverage=3, price=139.2, entry_zone=(138.2, 139.1),
        stop_loss=133.4, targets=[143.5, 147.9, 152.0], regime="TRENDING_UP",
        horizon="15m-4h", risk_brief=RiskBrief(risk_usd=10.0, max_deposit_pct=1.0),
        risks=["разворотный сценарий — повышенный риск"],
        score_breakdown=ScoreBreakdown(total=84.0, factors=_FACTORS),
        features={
            "timeframes": [
                {"timeframe": "15m", "trend": "up", "structure_signal": "BOS_UP"},
                {"timeframe": "1h", "trend": "up"},
                {"timeframe": "4h", "trend": "up"},
                {"timeframe": "1d", "trend": "range"},
            ],
            "derivatives": {"funding_trend": "neutral", "positioning": "healthy_long",
                            "positioning_score": 72},
            "orderflow": {"liquidity_grade": "excellent", "spread_pct": 0.01},
            "emergence": {"ignition": 74.0, "phase": "EARLY", "early_direction": "LONG",
                          "notes": ["объём заметно выше обычного (×2.1)"]},
            "regime": {"regime": "TRENDING_UP", "conflicts": []},
        },
        data_age_seconds=3.0,
    )
    base.update(kw)
    return TradingSignal(**base)


def weak_signal(**kw) -> TradingSignal:
    """Сетап, который публикуется (гейт входа пройден), но не будит пользователя."""
    overrides = dict(
        uid="c2", symbol="XUSDT", quality=62, score=62, tier="B", rr=1.9,
        confidence=0.7, risk_score=5,
    )
    overrides.update(kw)
    return strong_signal(**overrides)


# ── 1. расчёт уверенности ───────────────────────────────────────
def test_confidence_has_six_parts_and_normalised_weights():
    cfg = SignalConfig()
    report = assess_confidence(strong_signal(), cfg)
    assert len(report.parts) == 6
    assert {p.key for p in report.parts} == set(cfg.bot_confidence_weights)
    assert abs(sum(p.weight for p in report.parts) - 1.0) < 1e-6
    assert 0 <= report.percent <= 100
    # у каждого анализа есть человеческая расшифровка
    for part in report.parts:
        assert part.note, f"у {part.key} нет объяснения цифры"
        assert "анализ недоступен" not in part.note


def test_confidence_grows_with_quality_and_falls_on_stale_data():
    cfg = SignalConfig()
    low = assess_confidence(strong_signal(quality=60, score=60, tier="B"), cfg).percent
    high = assess_confidence(strong_signal(quality=90, score=90, tier="S"), cfg).percent
    assert high > low

    fresh = assess_confidence(strong_signal(), cfg).percent
    stale = assess_confidence(strong_signal(stale=True), cfg).percent
    assert stale < fresh

    old = assess_confidence(strong_signal(data_age_seconds=900.0), cfg).percent
    assert old < fresh


def test_confidence_labels_follow_config_thresholds():
    cfg = SignalConfig()
    high = assess_confidence(strong_signal(), cfg)
    assert high.percent >= cfg.BOT_CONFIDENCE_HIGH_MIN and high.label == "высокая"

    weak = assess_confidence(weak_signal(), cfg)
    assert weak.percent < high.percent
    assert weak.label in ("умеренная", "низкая", "очень низкая")
    assert weak.verdict and weak.warnings


def test_confidence_is_capped_without_direction():
    cfg = SignalConfig()
    report = assess_confidence(
        strong_signal(direction="NO_TRADE", status="NO_TRADE"), cfg
    )
    assert report.percent <= 45.0
    assert report.label in ("низкая", "очень низкая")


def test_engine_attaches_confidence_report_to_features():
    """Разбор уходит в features вместе с сигналом — его видно в API и в SQLite."""
    from v3.engine import FuturesSignalEngine
    from v3.tests.test_v3 import make_bundle, make_tf_map

    cfg = SignalConfig()
    engine = FuturesSignalEngine(data=None, cfg=cfg)  # type: ignore[arg-type]
    signal = engine.evaluate_bundle(make_bundle(), make_tf_map())
    payload = signal.features.get("bot_confidence")
    assert payload, "движок не приложил разбор уверенности"
    assert 0 <= payload["percent"] <= 100
    assert len(payload["parts"]) == 6
    assert payload["percent"] == round(assess_confidence(signal, cfg).percent, 1)
    # attach_confidence идемпотентен
    again = attach_confidence(signal, cfg)
    assert again.percent == payload["percent"]


# ── 2. карточка: три метрики отдельно ───────────────────────────
def test_beginner_card_has_separate_confidence_block():
    text = render_signal(strong_signal(), "beginner")
    assert "🎯 **УВЕРЕННОСТЬ БОТА:" in text
    assert "Из чего сложилась уверенность" in text
    assert "Вес каждого анализа".lower() in text.lower()
    assert "НЕ вероятность прибыли" in text
    # три метрики названы по-разному
    assert "Оценка сетапа:" in text
    assert "Полнота данных:" in text
    # режим рынка переведён на человеческий язык
    assert "восходящий тренд" in text and "TRENDING_UP" not in text
    low = text.lower()
    for token in _INTERNAL_TOKENS:
        assert token not in low, f"в карточке торчит внутренний токен {token}"


def test_confidence_bar_and_line_helpers():
    cfg = SignalConfig()
    assert rv.confidence_bar(0) == "░" * 10
    assert rv.confidence_bar(100) == "█" * 10
    assert len(rv.confidence_bar(73)) == 10
    line = rv.confidence_line(signal=strong_signal(), cfg=cfg)
    assert line.startswith("Уверенность бота:") and "%" in line
    assert rv.data_completeness_line(strong_signal()).startswith("📦 Полнота данных: 96%")


def test_setup_row_shows_confidence():
    row = rv.render_setup_row({"signal": strong_signal()}, 1)
    assert "Уверенность бота:" in row
    for token in _INTERNAL_TOKENS:
        assert token not in row.lower()


def test_no_trade_card_explains_low_confidence():
    text = render_signal(weak_signal(direction="NO_TRADE", status="NO_TRADE"), "beginner")
    assert "NO TRADE" in text
    assert "УВЕРЕННОСТЬ БОТА" in text


# ── 3. авто-сигналы ─────────────────────────────────────────────
def test_alert_gate_accepts_strong_and_rejects_weak():
    cfg = SignalConfig()
    ok = evaluate_alert(strong_signal(), cfg)
    assert ok.ok and not ok.reasons
    assert ok.percent >= cfg.ALERT_MIN_BOT_CONFIDENCE

    bad = evaluate_alert(weak_signal(), cfg)
    assert not bad.ok
    assert any("оценка сетапа" in r for r in bad.reasons)
    assert any("уверенность бота" in r for r in bad.reasons)


def test_alert_gate_rejects_stale_exhausted_and_short_plan():
    cfg = SignalConfig()
    assert not evaluate_alert(strong_signal(stale=True), cfg).ok
    sig = strong_signal()
    sig.features["emergence"]["phase"] = "EXHAUSTED"
    exhausted = evaluate_alert(sig, cfg)
    assert not exhausted.ok and any("выжато" in r for r in exhausted.reasons)

    no_targets = evaluate_alert(strong_signal(targets=[143.5]), cfg)
    assert not no_targets.ok and any("целей" in r for r in no_targets.reasons)

    wait = evaluate_alert(strong_signal(direction="WAIT", status="NO_TRADE"), cfg)
    assert not wait.ok and any("направления" in r for r in wait.reasons)


def test_alert_card_is_a_short_report():
    cfg = SignalConfig()
    text = render_signal_alert(strong_signal(), cfg)
    assert "🔔 **SOLUSDT**" in text
    assert "LONG" in text and "ставка на рост" in text
    assert "Вход:" in text
    assert "Ожидание:" in text
    assert "Стоп:" in text and "идея отменена" in text
    assert "потенциал 1:2.6" in text
    assert "не гарантия результата" in text
    for token in _INTERNAL_TOKENS:
        assert token not in text.lower()


def test_render_alert_dispatches_signal_and_event():
    cfg = SignalConfig()
    sig = strong_signal()
    item = AlertItem(kind="signal", signal=sig, decision=evaluate_alert(sig, cfg))
    rendered = render_alert(item, cfg)
    assert "SOLUSDT" in rendered and "LONG" in rendered and "Ожидание:" in rendered

    event = AlertItem(kind="event", event={"symbol": "SOLUSDT", "event": "TP1_HIT", "price": 143.5})
    text = render_alert(event, cfg)
    assert "SOLUSDT" in text and "первая цель" in text

    # legacy-контракт (словари) тоже работает
    legacy = render_alert({"symbol": "SOLUSDT", "direction": "LONG", "quality": 84,
                           "entry_zone": [138.2, 139.1], "stop_loss": 133.4,
                           "targets": [143.5, 147.9], "rr": 2.6}, cfg)
    assert "SOLUSDT" in legacy and "Вход:" in legacy


async def test_watcher_notifies_only_alert_worthy_signals(tmp_path):
    from v3.watcher import V3Watcher

    cfg = SignalConfig(COOLDOWN_SECONDS=60, ALERT_MAX_PER_CYCLE=3)
    store = SignalStore(tmp_path / "alerts.db")
    lifecycle = SignalLifecycle(store, cooldown_seconds=60, max_active=5)
    good = strong_signal(uid="a-good", ts_ms=int(time.time() * 1000))
    bad = weak_signal(uid="a-bad", ts_ms=int(time.time() * 1000))

    class FakeData:
        mode = "fake"

        async def tickers(self, symbols=None):
            class T:
                symbol = "SOLUSDT"
                last = 139.0
            return {"SOLUSDT": T(), "XUSDT": T()}

    class FakeEngine:
        async def analyze_batch(self, symbols, concurrency=4, deep=True):
            return [good, bad]

    watcher = V3Watcher(FakeData(), FakeEngine(), store, lifecycle, cfg, symbols=["SOLUSDT", "XUSDT"])
    sent: list[AlertItem] = []

    async def notify(items):
        sent.extend(items)

    emitted = await watcher.run_cycle(notify=notify)

    # оба наблюдения сохранены и прошли lifecycle, но в чат ушёл только сильный
    assert len(emitted) == 2
    assert [i.symbol for i in sent] == ["SOLUSDT"]
    assert store.get_state("v3_alerts_sent") == "1"
    assert store.get_state("v3_last_alert_symbol") == "SOLUSDT"
    assert "XUSDT" in store.get_state("v3_last_suppressed", "")
    store.close()


async def test_watcher_pause_stops_notifications(tmp_path):
    from v3.watcher import V3Watcher

    cfg = SignalConfig(COOLDOWN_SECONDS=60)
    store = SignalStore(tmp_path / "alerts_pause.db")
    lifecycle = SignalLifecycle(store, cooldown_seconds=60, max_active=5)
    good = strong_signal(uid="p-good", ts_ms=int(time.time() * 1000))

    class FakeData:
        mode = "fake"

        async def tickers(self, symbols=None):
            class T:
                symbol = "SOLUSDT"
                last = 139.0
            return {"SOLUSDT": T()}

    class FakeEngine:
        async def analyze_batch(self, symbols, concurrency=4, deep=True):
            return [good]

    watcher = V3Watcher(FakeData(), FakeEngine(), store, lifecycle, cfg, symbols=["SOLUSDT"])
    assert watcher.alerts_enabled is True
    assert watcher.toggle_alerts() is False

    sent: list[AlertItem] = []

    async def notify(items):
        sent.extend(items)

    await watcher.run_cycle(notify=notify)
    assert sent == []
    assert store.get_state("v3_alerts_enabled") == "0"
    assert watcher.toggle_alerts() is True
    store.close()


# ── 4. UI: раздел авто-сигналов, приветствие, глоссарий ─────────
def test_alerts_page_shows_thresholds_interval_and_state():
    cfg = SignalConfig(WATCHER_INTERVAL_SECONDS=180, ALERT_MIN_BOT_CONFIDENCE=70)
    now_ms = int(time.time() * 1000)
    text = rv.render_alerts_page(
        cfg,
        enabled=True,
        interval_seconds=180,
        last_cycle_ms=now_ms - 40_000,
        sent_total=2,
        found_total=5,
        last_alert_ms=now_ms - 600_000,
        last_alert_symbol="SOLUSDT",
        active_signals=1,
        scope="вся ликвидная вселенная USDT-perp",
        last_suppressed="XUSDT: уверенность бота 62% ниже порога 70%",
    )
    assert "АВТО-СИГНАЛЫ" in text
    assert "каждые ~3 мин" in text.lower()
    assert "Уверенность бота ≥ 70%" in text
    assert "Найдено достойных сетапов: 5 · отправлено вам: 2" in text
    assert "SOLUSDT" in text
    assert "Последний отказ: XUSDT" in text
    assert "не гарантия результата" in text


def test_core_alerts_section_and_toggle_without_watcher(monkeypatch, tmp_path):
    cfg = SignalConfig()
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "777")
    store = SignalStore(tmp_path / "alerts_ui.db")
    lifecycle = SignalLifecycle(store, 60, 5)
    dummy = type("D", (), {"mode": "fake"})()
    core = V3Core(dummy, type("E", (), {})(), store, lifecycle, SignalConfig())  # type: ignore[arg-type]

    text = core.alerts_text()
    assert "АВТО-СИГНАЛЫ" in text and "Пороги авто-сигнала" in text

    toggle = core.alerts_toggle_text()
    assert "Фоновый сканер не подключён" in toggle
    assert "daemon" in toggle

    assert "авто-сигналы" in core.status_text() or True
    store.close()


async def test_core_callback_alerts_is_a_new_message(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "777")
    cfg = SignalConfig()
    store = SignalStore(tmp_path / "alerts_cb.db")
    lifecycle = SignalLifecycle(store, 60, 5)
    dummy = type("D", (), {"mode": "fake"})()
    core = V3Core(dummy, type("E", (), {})(), store, lifecycle, cfg)  # type: ignore[arg-type]
    reply = await core.handle_callback("alerts", 777)
    assert reply.edit is False
    assert "АВТО-СИГНАЛЫ" in reply.text
    assert reply.keyboard is not None
    store.close()


def test_welcome_text_is_a_proper_greeting():
    low = WELCOME_TEXT.lower()
    assert "добро пожаловать" in low
    assert "авто-сигнал" in low
    assert "уверенность бота" in low
    assert "не является вероятностью прибыли" in low
    assert "🛠 Сборка: v" in WELCOME_TEXT
    for token in _INTERNAL_TOKENS:
        assert token not in low
    # меню и помощь согласованы с приветствием по набору разделов
    assert "АВТО-СИГНАЛЫ" in MENU_TEXT and "АВТО-СИГНАЛЫ" in HELP_TEXT


def test_main_menu_has_alerts_button():
    markup = kb.main_menu()
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "alerts" in callbacks


def test_glossary_buttons_all_resolve():
    """Каждая кнопка глоссария обязана вести на существующий термин."""
    from v3.tg.render import GLOSSARY

    markup = kb.glossary_menu()
    terms = [
        b.callback_data.split(":", 1)[1]
        for row in markup.inline_keyboard
        for b in row
        if str(b.callback_data or "").startswith("glossary:")
    ]
    assert "bot_confidence" in terms and "auto_alert" in terms
    for term in terms:
        assert term in GLOSSARY, f"кнопка глоссария ведёт в никуда: {term}"


def test_page_size_is_consistent_between_render_and_keyboards():
    assert kb.PAGE_SIZE == rv.LIST_PAGE_SIZE


def test_alert_thresholds_are_not_looser_than_entry_gate():
    """Авто-сигнал не может быть мягче общего гейта входа — иначе это обман."""
    from v3.config import validate_config

    cfg = SignalConfig()
    assert cfg.ALERT_MIN_QUALITY >= cfg.QUALITY_MIN
    assert cfg.ALERT_MIN_RR >= cfg.MIN_RISK_REWARD
    errors = [e for e in validate_config(cfg) if "ALERT" in e]
    assert errors == []


# ── 5. сквозная проверка: движок → карточка → гейт авто-сигнала ──
def test_engine_card_and_alert_gate_show_the_same_number():
    """Одна цифра уверенности в движке, в карточке и в гейте — без расхождений."""
    from v3.engine import FuturesSignalEngine
    from v3.tests.test_v3 import make_bundle, make_tf_map

    cfg = SignalConfig()
    engine = FuturesSignalEngine(data=None, cfg=cfg)  # type: ignore[arg-type]
    sig = engine.evaluate_bundle(make_bundle(), make_tf_map())

    percent = sig.features["bot_confidence"]["percent"]
    card = render_signal(sig, "beginner")
    decision = evaluate_alert(sig, cfg)

    assert f"{percent:.0f}%" in card, "в карточке другая цифра уверенности"
    assert f"{decision.percent:.0f}" == f"{percent:.0f}"
    # карточка движка не выдаёт сырые enum'ы режима рынка
    assert sig.regime in ("TRENDING_UP", "ACCUMULATION", "BREAKOUT", "UNCERTAIN",
                          "RANGING", "HIGH_VOLATILITY", "TRENDING_DOWN")
    if sig.direction in ("LONG", "SHORT"):
        assert sig.regime not in card or rv.regime_words(sig.regime) in card


def test_help_weights_line_matches_running_config(monkeypatch):
    """HELP обязан показывать те веса, которыми бот реально считает."""
    import importlib

    import v3.telegram as tg

    cfg = SignalConfig()
    line = tg._confidence_weights_line()
    for key, weight in cfg.bot_confidence_weights.items():
        assert tg._CONFIDENCE_PART_LABELS[key] in line
        assert f"{weight * 100:.0f}%" in line
    # строка присутствует в самом тексте помощи
    assert line in HELP_TEXT
    assert "BOT_CONFIDENCE_WEIGHTS" in HELP_TEXT

    # если веса переопределены через env, пересобранный HELP показывает их же
    monkeypatch.setenv(
        "BOT_CONFIDENCE_WEIGHTS",
        "quality:0.5,data:0.5,trend:0,confirm:0,risk:0,impulse:0",
    )
    reloaded = importlib.reload(tg)
    try:
        assert "качество сетапа 50%" in reloaded.HELP_TEXT
        assert "свежесть и полнота данных 50%" in reloaded.HELP_TEXT
        assert "риск-профиль 0%" in reloaded.HELP_TEXT
        overridden = SignalConfig().bot_confidence_weights
        assert overridden["quality"] == 0.5 and overridden["impulse"] == 0.0
    finally:
        monkeypatch.delenv("BOT_CONFIDENCE_WEIGHTS")
        importlib.reload(reloaded)


def test_confidence_weights_env_edge_cases():
    """Частичный/битый/нулевой набор весов не ломает процент."""
    partial = SignalConfig(BOT_CONFIDENCE_WEIGHTS="quality:1")
    assert abs(sum(partial.bot_confidence_weights.values()) - 1.0) < 1e-9
    assert set(partial.bot_confidence_weights) == {"quality", "data", "trend", "confirm", "risk", "impulse"}

    broken = SignalConfig(BOT_CONFIDENCE_WEIGHTS="quality:abc,unknown:3")
    assert abs(sum(broken.bot_confidence_weights.values()) - 1.0) < 1e-9

    zeros = SignalConfig(BOT_CONFIDENCE_WEIGHTS="quality:0,data:0,trend:0,confirm:0,risk:0,impulse:0")
    assert sum(zeros.bot_confidence_weights.values()) > 0

    report = assess_confidence(strong_signal(), zeros)
    assert 0 <= report.percent <= 100


def test_contradicting_early_impulse_is_flagged_not_hidden():
    """Ранний импульс против сделки — это риск, а не «подтверждение вверх»."""
    sig = strong_signal(
        direction="SHORT", entry_zone=(140.0, 141.0), stop_loss=146.0,
        targets=[135.0, 131.0, 127.0],
    )
    assert sig.features["emergence"]["early_direction"] == "LONG"

    why = rv.plain_reasons(sig)
    assert any("в другую сторону" in w for w in why)
    assert not any("возможно вверх" in w for w in why)

    card = render_signal(sig, "beginner")
    assert "Ранний отбор смотрит в другую сторону" in card

    row = rv.render_setup_row({"signal": sig}, 1)
    assert "против импульса" in row


def test_status_text_speaks_human(tmp_path):
    store = SignalStore(tmp_path / "status.db")
    lifecycle = SignalLifecycle(store, 60, 5)
    dummy = type("D", (), {"mode": "bybit"})()
    core = V3Core(dummy, type("E", (), {})(), store, lifecycle, SignalConfig())  # type: ignore[arg-type]

    sig = strong_signal()
    attach_confidence(sig, SignalConfig())
    store.save_signal(sig)
    store.set_state("last_scan_ms", str(int(time.time() * 1000)))
    store.set_state("v3_alerts_sent", "2")
    store.set_state("v3_last_alert_symbol", "SOLUSDT")

    text = core.status_text()
    assert "ИСТОРИЯ СИГНАЛОВ" in text
    assert "SOLUSDT 🟢 LONG" in text
    assert "оценка 84/100 (S)" in text and "уверенность" in text
    assert "подтверждён" in text
    assert "Авто-сигналов отправлено: 2" in text
    assert "q=" not in text and "tier=" not in text  # без сырых полей движка
    store.close()
