from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from telegram_bot.market import BybitMarketService, format_snapshot
from telegram_bot.ui import analysis_menu, main_menu


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    market = BybitMarketService()

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "<b>Crypto Futures Intelligence</b>\n\n"
            "Market data → derivatives → quant analysis → risk → signals.\n\n"
            "Signals are suppressed when reliable data or statistical edge is insufficient.",
            reply_markup=main_menu(),
        )

    @dp.message(Command("market"))
    async def market_command(message: Message) -> None:
        try:
            snapshot = await market.snapshot("BTCUSDT")
            await message.answer(format_snapshot(snapshot), reply_markup=analysis_menu("BTCUSDT"))
        except Exception:
            await message.answer("⚠️ Market data temporarily unavailable. Signal suppressed.")

    @dp.message(Command("analyze"))
    async def analyze_command(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        symbol = (parts[1].upper().replace("/", "") if len(parts) == 2 else "BTCUSDT")
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        try:
            snapshot = await market.snapshot(symbol)
            await message.answer(format_snapshot(snapshot), reply_markup=analysis_menu(symbol))
        except Exception:
            await message.answer(f"⚠️ Reliable market data for {symbol} is unavailable. Signal suppressed.")

    @dp.callback_query(F.data == "home")
    async def home(callback: CallbackQuery) -> None:
        await callback.message.edit_text("<b>Crypto Futures Intelligence</b>", reply_markup=main_menu())
        await callback.answer()

    @dp.callback_query(F.data.startswith("asset:"))
    async def asset(callback: CallbackQuery) -> None:
        symbol = callback.data.split(":", 1)[1]
        try:
            snapshot = await market.snapshot(symbol)
            await callback.message.edit_text(format_snapshot(snapshot), reply_markup=analysis_menu(symbol))
        except Exception:
            await callback.message.edit_text("⚠️ Live data unavailable. Signal suppressed.", reply_markup=main_menu())
        await callback.answer()

    @dp.callback_query(F.data == "market")
    async def market_button(callback: CallbackQuery) -> None:
        try:
            snapshot = await market.snapshot("BTCUSDT")
            await callback.message.edit_text(format_snapshot(snapshot), reply_markup=analysis_menu("BTCUSDT"))
        except Exception:
            await callback.message.edit_text("⚠️ Market data unavailable.", reply_markup=main_menu())
        await callback.answer()

    @dp.callback_query()
    async def coming_soon(callback: CallbackQuery) -> None:
        await callback.answer("This module is being connected to the live quant engine.", show_alert=True)

    return dp


async def run(token: str) -> None:
    bot = Bot(token=token)
    dp = build_dispatcher()
    await dp.start_polling(bot)
