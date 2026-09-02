"""Inline keyboards + callback payloads for the HYPE Telegram UI.

Callback payloads are flat ASCII strings:

  menu                      -> main menu
  scan                      -> run full market scan
  list:top:<page>           -> paginated TOP setups
  list:longs:<page>         -> paginated LONG setups
  list:shorts:<page>        -> paginated SHORT setups
  coin:<SYMBOL>             -> full analysis card for a symbol
  update:<SYMBOL>           -> force refresh + re-render card
  pro:<SYMBOL>              -> pro-mode card of the last analysis
  market                    -> market overview ("Мой рынок")
  pick:<page>               -> list liquid symbols to analyse
  glossary:<TERM>           -> simple explanation of a term
  help                      -> help text
  settings                  -> settings menu
  set:mode:<mode>           -> beginner | pro
  set:deposit:<amount>      -> deposit preset
  set:risk:<pct>            -> risk-per-trade preset
  dep_custom                -> ask the user for a custom deposit
  alerts                    -> auto-signal section (status + thresholds)
  alerts:toggle             -> pause / resume auto-signals
  alerts:now                -> run one check cycle right now
  back:<page>               -> go back (page = menu | top | longs | shorts)
"""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from v3.tg.render import LIST_PAGE_SIZE

# Единая константа: рендер списков (v3.tg.render) и пагинация обязаны совпадать.
PAGE_SIZE = LIST_PAGE_SIZE


def _btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🔎 СКАНИРОВАТЬ РЫНОК", "scan")],
        [_btn("🔥 ЛУЧШИЕ LONG", "list:longs:0"), _btn("🔻 ЛУЧШИЕ SHORT", "list:shorts:0")],
        [_btn("⭐ ТОП ВОЗМОЖНОСТИ", "list:top:0"), _btn("🔍 АНАЛИЗ МОНЕТЫ", "pick:0")],
        [_btn("🔔 АВТО-СИГНАЛЫ", "alerts")],
        [_btn("📊 МОЙ РЫНОК", "market"), _btn("⚙️ НАСТРОЙКИ", "settings")],
        [_btn("📚 ПОМОЩЬ", "help")],
    ])


def alerts_menu(enabled: bool = True) -> InlineKeyboardMarkup:
    """Раздел авто-сигналов: пауза/вкл, немедленная проверка, навигация."""
    toggle = _btn("⏸ Поставить на паузу", "alerts:toggle") if enabled else _btn("▶️ Включить", "alerts:toggle")
    return InlineKeyboardMarkup(inline_keyboard=[
        [toggle, _btn("🔎 Проверить сейчас", "alerts:now")],
        [_btn("🔎 Скан рынка", "scan"), _btn("⭐ ТОП ВОЗМОЖНОСТИ", "list:top:0")],
        [_btn("🏠 Главная", "menu")],
    ])


def scan_action() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🧠 АНАЛИЗ РЫНКА", "scan")],
        [_btn("📊 МОЙ РЫНОК", "market"), _btn("⭐ ТОП ВОЗМОЖНОСТИ", "list:top:0")],
        [_btn("🏠 Главная", "menu")],
    ])


def scan_results(has_long: bool, has_short: bool, has_top: bool) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if has_top:
        buttons.append([_btn("⭐ ТОП ВОЗМОЖНОСТИ", "list:top:0")])
    if has_long or has_short:
        row: list[InlineKeyboardButton] = []
        if has_long:
            row.append(_btn("🔥 ЛУЧШИЕ LONG", "list:longs:0"))
        if has_short:
            row.append(_btn("🔻 ЛУЧШИЕ SHORT", "list:shorts:0"))
        buttons.append(row)
    buttons.append([_btn("🧠 СКАН ЗАНОВО", "scan"), _btn("🏠 Главная", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def setups_pager(kind: str, page: int, pages: int) -> InlineKeyboardMarkup:
    """kind: top | longs | shorts."""
    title = {"top": "⭐ ТОП ВОЗМОЖНОСТИ", "longs": "🔥 ЛУЧШИЕ LONG", "shorts": "🔻 ЛУЧШИЕ SHORT"}.get(kind, kind)
    row: list[InlineKeyboardButton] = []
    if page > 0:
        row.append(_btn("◀️ Назад", f"list:{kind}:{page - 1}"))
    if page < pages - 1:
        row.append(_btn("Вперёд ▶️", f"list:{kind}:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        row or [_btn("•", "menu")],
        [_btn("🔎 Скан рынка", "scan"), _btn(f"{title}", "list:top:0")],
        [_btn("🏠 Главная", "menu")],
    ])


def coin_card(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🔄 ОБНОВИТЬ", f"update:{symbol}"), _btn("📈 PRO", f"pro:{symbol}")],
        [_btn("❓ Что это?", "help"), _btn("🏠 Главная", "menu")],
    ])


def coin_list(symbols: list[str], page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(symbols), 2):
        chunk = symbols[i : i + 2]
        rows.append([_btn(s, f"coin:{s}") for s in chunk])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("◀️", f"pick:{page - 1}"))
    if page < pages - 1:
        nav.append(_btn("▶️", f"pick:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([_btn("🏠 Главная", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_menu(settings: dict[str, Any]) -> InlineKeyboardMarkup:
    mode = settings.get("mode", "beginner")
    deposit = float(settings.get("deposit_usd", 1000.0))
    risk = float(settings.get("risk_per_trade_pct", 1.0))
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"🧠 Режим: {'PRO' if mode == 'pro' else 'BEGINNER'}", f"set:mode:{'pro' if mode != 'pro' else 'beginner'}")],
        [_btn(f"💰 Депозит: ${deposit:,.0f}", "set:deposit:custom")],
        [_btn(f"⚠️ Риск/сделку: {risk:g}%", "set:risk")],
        [_btn("🏠 Главная", "menu")],
    ])


def deposit_presets() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("$100", "set:deposit:100"), _btn("$500", "set:deposit:500"), _btn("$1000", "set:deposit:1000")],
        [_btn("$5000", "set:deposit:5000"), _btn("✏️ Своё", "dep_custom")],
        [_btn("↩️ Назад", "settings")],
    ])


def risk_presets() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("0.5% (консерв.)", "set:risk:0.5"), _btn("1% (стандарт)", "set:risk:1")],
        [_btn("2% (агрессивно)", "set:risk:2"), _btn("↩️ Назад", "settings")],
    ])


def glossary_menu() -> InlineKeyboardMarkup:
    # Первые кнопки — то, что новичок видит в каждой карточке сигнала.
    terms = [
        ("🎯 Уверенность бота", "bot_confidence"), ("⭐ Оценка сетапа", "score"),
        ("📦 Полнота данных", "data_completeness"), ("🔔 Авто-сигнал", "auto_alert"),
        ("⚡ Намечается движение", "emergence"), ("🧭 Реджим", "regime"),
        ("RSI", "rsi"), ("ATR", "atr"), ("ADX", "adx"), ("BOS/CHoCH", "bos"),
        ("Фандинг", "funding"), ("Open Interest", "oi"), ("R:R", "rr"),
        ("VWAP", "vwap"), ("Ликвидность", "liquidity"), ("Стоп и цели", "tp"),
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(terms), 2):
        rows.append([_btn(t, f"glossary:{k}") for t, k in terms[i : i + 2]])
    rows.append([_btn("🏠 Главная", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def glossary_back(term: str = "") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("↩️ К глоссарию", "glossary:list"), _btn("🏠 Главная", "menu")],
    ])
