"""Задача 2: история диалога Telegram.

Независимые запросы (меню/скан/рынок/монета/настройки) — ВСЕГДА новое сообщение
(``BotReply.edit == False``); навигация внутри одного результата (пагинация,
переключение PRO, «🔄 ОБНОВИТЬ») — ``edit == True``. Удаление сообщений — никогда.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from v3.config import SignalConfig
from v3.store import SignalLifecycle, SignalStore
from v3.telegram import V3Core


class _FakeData:
    mode = "fake"

    async def tickers(self, symbols=None, force=False):
        return {}

    async def market_overview(self):
        return {"mode": "fake", "ts_ms": 1, "btc": None, "eth": None, "universe_count": 0}

    def source_diagnostics(self):
        return []


class _FakeEngine:
    class _D:
        mode = "fake"

    data = _D()

    async def analyze_batch(self, symbols, concurrency=4, deep=True):
        return []

    async def analyze(self, symbol, refresh=False, deep=True):
        raise RuntimeError("offline test engine")


@pytest.fixture()
def cfg(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "321")
    return SignalConfig()


def _core(cfg: SignalConfig) -> V3Core:
    store = SignalStore("/tmp/v3_test_history.db")
    lifecycle = SignalLifecycle(store, cooldown_seconds=60, max_active=10)
    return V3Core(_FakeData(), _FakeEngine(), store, lifecycle, cfg)  # type: ignore[arg-type]


# ── флаги edit по типам запросов ────────────────────────────────
async def test_independent_requests_are_new_messages(cfg):
    core = _core(cfg)
    assert (await core.handle_callback("menu", None)).edit is False
    assert (await core.handle_callback("help", None)).edit is False
    assert (await core.handle_callback("market", None)).edit is False
    assert (await core.handle_callback("settings", 321)).edit is False
    assert (await core.handle_callback("coin:BTCUSDT", None)).edit is False
    assert (await core.handle_callback("pick:0", None)).edit is False
    assert (await core.handle_callback("unknown-callback", None)).edit is False


async def test_navigation_inside_result_is_edit(cfg):
    core = _core(cfg)
    # пагинация списков — правим то же сообщение
    assert (await core.handle_callback("list:longs:0", None)).edit is True
    assert (await core.handle_callback("list:shorts:0", None)).edit is True
    assert (await core.handle_callback("list:top:0", None)).edit is True
    # «🔄 ОБНОВИТЬ» карточки — правим карточку
    assert (await core.handle_callback("update:BTCUSDT", None)).edit is True
    # переключение вида карточки PRO — правим
    assert (await core.handle_callback("pro:BTCUSDT", None)).edit is True
    # навигация по словарю терминов — правим
    assert (await core.handle_callback("glossary:rsi", None)).edit is True
    assert (await core.handle_callback("glossary:list", None)).edit is True
    # настройки: выбор пресета в том же сообщении
    assert (await core.handle_callback("set:mode:pro", 321)).edit is True
    assert (await core.handle_callback("set:deposit:1000", 321)).edit is True
    assert (await core.handle_callback("set:risk:1", 321)).edit is True
    # назад в меню — навигация
    assert (await core.handle_callback("back:menu", None)).edit is True


async def test_scan_is_always_new_message(cfg):
    core = _core(cfg)
    r1 = await core.handle_callback("scan", None)
    r2 = await core.handle_callback("scan", None)
    assert r1.edit is False
    assert r2.edit is False
    assert "СКАН" in r1.text or "НЕТ РЕАЛЬНЫХ ДАННЫХ" in r1.text


# ── фейк-транспорт: исполнение BotReply ─────────────────────────
class FakeAnswer:
    __test__ = False

    def __init__(self, text: str) -> None:
        self.text = text


class FakeMessage:
    __test__ = False

    def __init__(self, fail_edit: bool = False) -> None:
        self.fail_edit = fail_edit
        self.answered: list[str] = []
        self.edited: list[str] = []
        self.deleted = False
        self.keyboards: list[object] = []

    async def answer(self, text: str, reply_markup=None, disable_web_page_preview=True):
        self.answered.append(text)
        self.keyboards.append(reply_markup)

    async def edit_text(self, text: str, reply_markup=None, disable_web_page_preview=True):
        if self.fail_edit:
            raise RuntimeError("Bad Request: message can't be edited")
        self.edited.append(text)
        self.keyboards.append(reply_markup)

    async def delete(self):  # охрана: вызываться не должен НИКОГДА
        self.deleted = True


class FakeQuery:
    __test__ = False

    def __init__(self, data: str, fail_edit: bool = False) -> None:
        self.data = data
        self.message = FakeMessage(fail_edit=fail_edit)
        self.from_user = SimpleNamespace(id=None)
        self.acknowledged = False

    async def answer(self, *args, **kwargs):
        self.acknowledged = True


async def test_transport_two_scans_two_answers_zero_edits(cfg):
    core = _core(cfg)
    q1, q2 = FakeQuery("scan"), FakeQuery("scan")
    await core.dispatch_callback(q1)
    await core.dispatch_callback(q2)
    assert len(q1.message.answered) == 1 and len(q2.message.answered) == 1
    assert q1.message.edited == [] and q2.message.edited == []
    assert q1.message.deleted is False and q2.message.deleted is False


async def test_transport_menu_is_new_message(cfg):
    core = _core(cfg)
    q = FakeQuery("menu")
    await core.dispatch_callback(q)
    assert len(q.message.answered) == 1
    assert q.message.edited == []
    assert q.message.deleted is False


async def test_transport_pagination_edits_not_answers(cfg):
    core = _core(cfg)
    q = FakeQuery("list:longs:0")
    await core.dispatch_callback(q)
    assert q.message.edited != [] and q.message.answered == []
    assert q.message.deleted is False


async def test_transport_update_edits_with_fallback_to_new(cfg):
    core = _core(cfg)
    q_ok = FakeQuery("update:BTCUSDT")
    await core.dispatch_callback(q_ok)
    assert (q_ok.message.edited or q_ok.message.answered) and not q_ok.message.deleted

    # edit упал (сообщение удалено пользователем и т.п.) → fallback на НОВОЕ сообщение
    q_fail = FakeQuery("update:BTCUSDT", fail_edit=True)
    await core.dispatch_callback(q_fail)
    assert q_fail.message.answered != []
    assert q_fail.message.edited == []
    assert q_fail.message.deleted is False


def test_no_delete_message_calls_anywhere():
    """Платформа никогда не удаляет сообщения из истории."""
    import inspect

    import v3.telegram as tg

    src = inspect.getsource(tg)
    assert ".delete_message(" not in src
    assert "message.delete(" not in src
