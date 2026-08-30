"""Тесты Telegram-интерфейса: клавиатуры, форматирование, настройки."""

import pytest

from src.analysis.spectrum import SpectrumAnalyzer
from src.data.demo import DemoMarketSource
from src.notify.telegram import (
    MAX_TEXT,
    Prefs,
    _norm_symbol,
    _short_card,
    _spectrum_text,
    _split,
    _verdict_reconciliation,
    kb_exchange,
    kb_gems,
    kb_lev,
    kb_main,
    kb_market,
    kb_prefs,
    kb_symbol,
    kb_watch,
)


def test_norm_symbol():
    assert _norm_symbol("sol") == "SOLUSDT"
    assert _norm_symbol("SOLUSDT") == "SOLUSDT"
    assert _norm_symbol(" sol/usdt ") == "SOLUSDT"
    assert _norm_symbol("") == ""
    assert _norm_symbol(None) == ""


def test_split_respects_telegram_limit():
    text = "\n".join(["строка номер %d" % i for i in range(1200)])
    chunks = _split(text)
    assert all(len(c) <= MAX_TEXT for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_short_text_not_split():
    assert _split("короткий текст") == ["короткий текст"]


def test_keyboards_build():
    """Все клавиатуры собираются и callback_data влезает в лимит 64 байта."""
    boards = [
        kb_main(),
        kb_watch([]),
        kb_symbol("BTCUSDT", True),
        kb_symbol("BTCUSDT", False),
        kb_gems([{"symbol": "PEPEUSDT", "score": 71.0, "direction": "LONG"}]),
        kb_prefs(),
        kb_lev(),
        kb_exchange(),
        kb_market(),
    ]
    for kb in boards:
        assert kb.inline_keyboard
        for row in kb.inline_keyboard:
            for btn in row:
                assert btn.text
                assert btn.callback_data is None or len(btn.callback_data) <= 64


def test_symbol_menu_has_all_actions():
    kb = kb_symbol("SOLUSDT", False)
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "deep:SOLUSDT" in data
    assert "plan:SOLUSDT" in data
    assert "chart:SOLUSDT" in data
    assert "spectrum:SOLUSDT" in data
    assert "backtest:SOLUSDT" in data
    assert "watch:SOLUSDT" in data
    assert "unwatch:SOLUSDT" not in data
    assert "unwatch:SOLUSDT" in [
        b.callback_data for row in kb_symbol("SOLUSDT", True).inline_keyboard for b in row
    ]


def test_main_menu_has_backtest_entry():
    data = [btn.callback_data for row in kb_main().inline_keyboard for btn in row]
    assert "menu:backtest" in data


def test_main_menu_has_engine_switch():
    """Первая строка kb_main() — кнопки переключения движка v1/v2."""
    first_row = kb_main().inline_keyboard[0]
    data = [btn.callback_data for btn in first_row]
    texts = [btn.text for btn in first_row]
    assert data == ["engine:v2", "engine:v1"]
    assert "🆕 Движок: v2" in texts
    assert "🧮 Движок: v1" in texts
    # влезает в лимит callback_data 64 байта
    for btn in first_row:
        assert len(btn.callback_data) <= 64


def test_kb_engine_reply_builds():
    """Постоянная reply-клавиатура переключения движка собирается."""
    from src.notify.telegram import ENGINE_BTN_V1, ENGINE_BTN_V2, kb_engine_reply

    kb = kb_engine_reply()
    assert kb.keyboard
    row = kb.keyboard[0]
    assert [btn.text for btn in row] == [ENGINE_BTN_V2, ENGINE_BTN_V1]


def test_backtest_report_renders():
    """Отчёт собирается из метрик и содержит честные предупреждения."""
    from src.backtest.engine import BacktestConfig, BacktestResult
    from src.backtest.report import backtest_report

    res = BacktestResult(symbol="BTCUSDT", config=BacktestConfig(), is_demo=True)
    res.metrics = {
        "total_trades": 40, "wins": 20, "losses": 20, "breakevens": 0, "win_rate": 50.0,
        "total_r": 4.0, "expectancy_r": 0.1, "avg_win_r": 1.5, "avg_loss_r": -1.0,
        "profit_factor": 1.5, "max_consecutive_losses": 4, "max_drawdown_r": 3.0,
        "avg_bars_held": 10.0, "breakeven_win_rate": 40.0, "edge_over_breakeven": 10.0,
        "buy_hold_pct": 12.5, "by_direction": {"LONG": {"trades": 40, "win_rate": 50.0,
        "total_r": 4.0, "expectancy_r": 0.1}}, "exit_reasons": {"target": 20, "stop_loss": 20},
        "fee_drag_r": 2.0, "median_stop_dist_pct": 1.8, "gap_stops": 2,
        "signals_generated": 100, "signals_passed_filters": 60, "fill_rate_pct": 66.7,
        "signal_directions": {"LONG": 100}, "verdict": "на грани",
    }
    txt = backtest_report(res)
    assert "БЭКТЕСТ BTCUSDT" in txt
    assert "Точка безубыточности" in txt
    assert "демо-данные" in txt
    assert "гэпом сквозь стоп" in txt
    assert "не торгует" in txt


def test_backtest_report_no_trades_is_honest():
    from src.backtest.engine import BacktestConfig, BacktestResult
    from src.backtest.report import backtest_report

    res = BacktestResult(symbol="BTCUSDT", config=BacktestConfig())
    res.metrics = {"total_trades": 0, "signals_generated": 12, "verdict": "нет сделок",
                   "skip_reasons": {"стоп": 12}}
    txt = backtest_report(res)
    assert "ни одной сделки" in txt.lower() or "не прошёл фильтры" in txt
    assert "стоп: 12" in txt


def test_prefs_defaults_and_roundtrip(settings):
    p = Prefs(settings)
    assert p.deposit == settings.DEFAULT_DEPOSIT_USD
    assert p.risk_pct == settings.RISK_PER_TRADE_PCT
    assert p.exchange in ("bybit", "binance")
    assert p.market in ("futures", "spot")
    assert "Депозит" in p.summary()

    p2 = Prefs(settings, {"deposit": 250.0, "risk_pct": 0.5, "leverage": 3, "exchange": "binance", "market": "spot"})
    assert p2.deposit == 250.0
    assert p2.leverage == 3
    assert p2.exchange == "binance"
    assert p2.market == "spot"
    assert Prefs(settings, p2.to_dict()).to_dict() == p2.to_dict()


def test_prefs_rejects_unknown_exchange(settings):
    p = Prefs(settings, {"exchange": "ftx", "market": "margin"})
    assert p.exchange == "bybit"
    assert p.market == "futures"


@pytest.mark.asyncio
async def test_short_card_text(settings):
    from src.analysis.engine import AnalysisEngine

    src = DemoMarketSource(settings)
    res = await AnalysisEngine(src, settings).analyze("BTCUSDT")
    text = _short_card(res)
    assert "BTCUSDT" in text
    assert "Цена" in text
    # HTML-разметка должна быть закрыта
    assert text.count("<b>") == text.count("</b>")


@pytest.mark.asyncio
async def test_spectrum_text(settings):
    src = DemoMarketSource(settings)
    rep = await SpectrumAnalyzer(src, settings).analyze("SOLUSDT")
    text = _spectrum_text(rep)
    assert "СПЕКТРАЛЬНЫЙ АНАЛИЗ SOLUSDT" in text
    assert "Таймфреймы" in text
    assert text.count("<b>") == text.count("</b>")
    assert len(text) < 4000 or len(_split(text)) > 1


def test_bot_registers_all_handlers(tmp_path):
    """Конструктор с токеном должен зарегистрировать и команды, и все кнопки."""
    from src.config.settings import Settings
    from src.notify.telegram import TelegramAdvisorBot

    s = Settings(
        _env_file=None,
        MARKET_DATA_MODE="demo",
        DATA_DIR=tmp_path,
        DB_PATH=tmp_path / "t.db",
        CHART_DIR=tmp_path / "charts",
        TELEGRAM_BOT_TOKEN="123456789:AAFakeTokenForHandlerRegistrationTest",
    )
    bot = TelegramAdvisorBot(s)
    assert bot.enabled is True

    msg_handlers = bot.router.message.handlers
    cb_handlers = bot.router.callback_query.handlers
    assert len(msg_handlers) >= 12, "должны быть зарегистрированы команды /start, /watch, /scan..."
    assert len(cb_handlers) >= 15, "должны быть зарегистрированы callback-кнопки"

    # все кнопочные хендлеры зарегистрированы
    cb_names = {h.callback.__name__ for h in cb_handlers}
    for expected in ("_home", "_watch_btn", "_scan_btn", "_gems_btn", "_market_btn",
                     "_news_btn", "_guide_btn", "_prefs_btn",
                     "_set_deposit", "_set_risk", "_set_lev", "_pick_lev",
                     "_set_exchange", "_pick_exchange", "_set_market", "_pick_market",
                     "_symbol_menu", "_deep", "_plan", "_chart", "_spectrum_btn",
                     "_backtest_btn", "_backtest_menu",
                     "_watch_add", "_watch_del", "_engine_btn"):
        assert expected in cb_names, expected

    # команды и FSM-ввод настроек
    msg_names = {h.callback.__name__ for h in msg_handlers}
    for expected in ("_start", "_watch_cmd", "_scan_cmd", "_signal_cmd", "_chart_cmd",
                     "_positions", "_news", "_guide", "_add", "_del",
                     "_get_deposit", "_get_risk", "_backtest_symbol",
                     "_engine_cmd", "_engine_reply_btn", "_free_text"):
        assert expected in msg_names, expected


def test_bot_disabled_without_token(tmp_path):
    from src.config.settings import Settings
    from src.notify.telegram import TelegramAdvisorBot

    s = Settings(
        _env_file=None, MARKET_DATA_MODE="demo", DATA_DIR=tmp_path, DB_PATH=tmp_path / "t.db",
        CHART_DIR=tmp_path / "charts", TELEGRAM_BOT_TOKEN="",
    )
    bot = TelegramAdvisorBot(s)
    assert bot.enabled is False
    assert bot.bot is None


def _demo_bot(tmp_path):
    from src.config.settings import Settings
    from src.notify.telegram import TelegramAdvisorBot

    s = Settings(
        _env_file=None, MARKET_DATA_MODE="demo", DATA_DIR=tmp_path, DB_PATH=tmp_path / "t.db",
        CHART_DIR=tmp_path / "charts", TELEGRAM_BOT_TOKEN="",
    )
    return TelegramAdvisorBot(s)


def test_engine_defaults_and_switch(tmp_path):
    """Движок по умолчанию v1, переключение сохраняется per-chat."""
    from src.notify.telegram import ENGINE_V1, ENGINE_V2

    bot = _demo_bot(tmp_path)
    chat = 101
    assert bot._engine(chat) == ENGINE_V1
    assert bot._set_engine(chat, ENGINE_V2) is True
    assert bot._engine(chat) == ENGINE_V2
    assert bot._set_engine(chat, ENGINE_V1) is True
    assert bot._engine(chat) == ENGINE_V1
    # невалидное значение не меняет движок
    assert bot._set_engine(chat, "zzz") is False
    assert bot._engine(chat) == ENGINE_V1
    # другой чат не затронут
    assert bot._engine(202) == ENGINE_V1


@pytest.mark.asyncio
async def test_v2_core_is_lazy_and_handles_0x(tmp_path):
    """AssistantCore поднимается лениво и в режиме v2 разбирает 0x-адрес."""
    bot = _demo_bot(tmp_path)
    chat = 303
    assert bot._v2_core is None
    core = bot._get_v2_core()
    assert core is not None
    assert bot._v2_core is core
    answer = await core.handle_message(
        "проанализируй 0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", chat_id=chat
    )
    assert "🛡️ Безопасность" in answer or "Вердикт" in answer
    await bot.stop()
    assert bot._v2_core is None


@pytest.mark.asyncio
async def test_verdict_reconciliation():
    """Расхождение спектра и плана должно объясняться, а не замалчиваться."""
    from types import SimpleNamespace

    def plan(direction):
        return SimpleNamespace(direction=direction)

    def spec(direction, score=0.4, conf=60.0):
        return SimpleNamespace(direction=direction, total_score=score, confluence=conf)

    # 1. совпадают
    assert "совпадают" in _verdict_reconciliation(plan("LONG"), spec("LONG"))

    # 2. план ждёт, спектр видит направление
    note = _verdict_reconciliation(plan("WAIT"), spec("LONG", 0.35, 55.0))
    assert "жёсткий фильтр плана не пройден" in note
    assert "LONG" in note

    # 3. план даёт сделку, спектр нейтрален
    note = _verdict_reconciliation(plan("SHORT"), spec("WAIT", 0.05, 20.0))
    assert "спектр нейтрален" in note
    assert "уменьшенный объём" in note

    # 4. прямой конфликт направлений
    note = _verdict_reconciliation(plan("LONG"), spec("SHORT", -0.5, 70.0))
    assert "Конфликт вердиктов" in note
    assert "не входить" in note

    # все ветки начинаются с переноса строки (приклеиваются к основному тексту)
    for p_dir in ("LONG", "SHORT", "WAIT"):
        for s_dir in ("LONG", "SHORT", "WAIT"):
            assert _verdict_reconciliation(plan(p_dir), spec(s_dir)).startswith("\n\n")
