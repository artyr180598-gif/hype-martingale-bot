"""Telegram keyboards — main menu + navigation."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔎 Сканировать рынок", callback_data="scan"),
    )
    builder.row(
        InlineKeyboardButton(text="🔥 Лучшие LONG", callback_data="top_long"),
        InlineKeyboardButton(text="🔻 Лучшие SHORT", callback_data="top_short"),
    )
    builder.row(
        InlineKeyboardButton(text="⭐ Топ возможности", callback_data="top_all"),
    )
    builder.row(
        InlineKeyboardButton(text="🔍 Анализ монеты", callback_data="analyze_coin"),
    )
    builder.row(
        InlineKeyboardButton(text="🔔 Авто-сигналы", callback_data="alerts"),
        InlineKeyboardButton(text="📊 Мой рынок", callback_data="market"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
        InlineKeyboardButton(text="📚 Помощь", callback_data="help"),
    )
    return builder.as_markup()


def back_to_main_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главная", callback_data="main")
    return builder.as_markup()


def signal_actions_kb(symbol: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"signal:{symbol}"),
        InlineKeyboardButton(text="📈 PRO разбор", callback_data=f"pro:{symbol}"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 График", callback_data=f"chart:{symbol}"),
        InlineKeyboardButton(text="🔔 Авто", callback_data="alerts"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главная", callback_data="main"),
    )
    return builder.as_markup()


def coin_list_kb(symbols: list[str], prefix: str = "signal") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for sym in symbols[:20]:
        builder.button(text=sym, callback_data=f"{prefix}:{sym}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="main"))
    return builder.as_markup()


def pagination_kb(page: int, has_next: bool, base_cb: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if page > 0:
        builder.button(text="⬅️ Назад", callback_data=f"{base_cb}:page:{page-1}")
    if has_next:
        builder.button(text="Вперед ➡️", callback_data=f"{base_cb}:page:{page+1}")
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="main"))
    return builder.as_markup()


def settings_kb(current_mode: str = "beginner") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Новичок" if current_mode == "beginner" else "Новичок",
            callback_data="settings:mode:beginner",
        ),
        InlineKeyboardButton(
            text="✅ PRO" if current_mode == "pro" else "PRO",
            callback_data="settings:mode:pro",
        ),
    )
    builder.row(
        InlineKeyboardButton(text="💰 Депозит", callback_data="settings:deposit"),
        InlineKeyboardButton(text="⚠️ Риск", callback_data="settings:risk"),
    )
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="main"))
    return builder.as_markup()


def alerts_kb(paused: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="▶️ Включить" if paused else "⏸ Пауза", callback_data="alerts:toggle"),
        InlineKeyboardButton(text="🔍 Проверить сейчас", callback_data="alerts:check"),
    )
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="main"))
    return builder.as_markup()
