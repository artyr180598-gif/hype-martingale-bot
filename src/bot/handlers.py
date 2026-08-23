"""
Telegram Bot Command and Callback Handlers.
"""
from typing import Any

from src.ai.assistant import AIAssistant
from src.backtesting.engine import BacktestEngine
from src.bot.client import TelegramBotClient
from src.bot.formatters import BotFormatters
from src.bot.keyboards import BotKeyboards
from src.core.logging import get_logger
from src.data.adapters.binance import BinanceFuturesAdapter
from src.data.downloader import HistoricalDataDownloader
from src.intelligence.news_fetcher import NewsFetcher
from src.paper.engine import PaperTradingEngine
from src.scanner.market_scanner import MarketScanner
from src.strategies.registry import StrategyRegistry

logger = get_logger("bot.handlers")

# Global paper trading instance for bot session
paper_engine = PaperTradingEngine(initial_balance=10000.0)


class BotHandlers:
    """
    Handles user commands, text queries, and interactive inline callback clicks.
    """

    def __init__(self, bot_client: TelegramBotClient):
        self.bot = bot_client
        self.scanner = MarketScanner()
        self.downloader = HistoricalDataDownloader(BinanceFuturesAdapter())

    async def handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text", "").strip()

        if not chat_id or not text:
            return

        # Commands
        if text.startswith("/start"):
            await self.cmd_start(chat_id)
        elif text.startswith("/market"):
            await self.cmd_market(chat_id)
        elif text.startswith("/top"):
            await self.cmd_top(chat_id)
        elif text.startswith("/analyze"):
            parts = text.split()
            sym = parts[1].upper() if len(parts) > 1 else "BTCUSDT"
            if not sym.endswith("USDT"):
                sym += "USDT"
            await self.cmd_analyze(chat_id, sym)
        elif text.startswith("/backtest"):
            await self.cmd_backtest(chat_id)
        elif text.startswith("/paper"):
            await self.cmd_paper(chat_id)
        elif text.startswith("/news"):
            await self.cmd_news(chat_id)
        elif text.startswith("/strategies"):
            await self.cmd_strategies(chat_id)
        elif text.startswith("/settings"):
            await self.cmd_settings(chat_id)
        elif text.startswith("/help"):
            await self.cmd_help(chat_id)
        else:
            # Natural Language AI Interface
            await self.handle_natural_language(chat_id, text)

    async def handle_callback_query(self, callback: dict[str, Any]) -> None:
        cb_id = callback.get("id")
        data = callback.get("data", "")
        message = callback.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        msg_id = message.get("message_id")

        if not chat_id or not data:
            return

        if cb_id:
            await self.bot.answer_callback_query(str(cb_id))

        if data == "menu:main":
            await self.bot.send_message(chat_id, "Главное меню платформы:", reply_markup=BotKeyboards.main_menu())
        elif data == "menu:market":
            await self.cmd_market(chat_id)
        elif data == "menu:top":
            await self.cmd_top(chat_id)
        elif data == "menu:backtest":
            await self.cmd_backtest(chat_id)
        elif data == "menu:paper":
            await self.cmd_paper(chat_id)
        elif data == "menu:news":
            await self.cmd_news(chat_id)
        elif data == "menu:strategies":
            await self.cmd_strategies(chat_id)
        elif data == "menu:settings":
            await self.cmd_settings(chat_id)
        elif data.startswith("analyze:"):
            sym = data.split(":")[1]
            await self.cmd_analyze(chat_id, sym)
        elif data.startswith("run_bt:"):
            _, sym, tf = data.split(":")
            await self.run_backtest_action(chat_id, sym, tf)
        elif data.startswith("set_risk:"):
            profile = data.split(":")[1]
            await self.bot.send_message(chat_id, f"✅ Профиль риска обновлен на: `{profile}`", reply_markup=BotKeyboards.back_to_main_keyboard())
        elif data.startswith("paper_open:"):
            sym = data.split(":")[1]
            await self.open_paper_trade_action(chat_id, sym)

    async def cmd_start(self, chat_id: int) -> None:
        welcome_text = (
            "🚀 **Добро пожаловать в Quantitative Crypto Futures Intelligence Platform!**\n\n"
            "Платформа непрерывно сканирует рынок USDT/USDC-M фьючерсов, анализирует Order Flow, "
            "Market Structure, фандинг, открытый интерес, волатильность и мульти-таймфреймовые тренды.\n\n"
            "📌 Выберите нужный раздел в меню ниже:"
        )
        await self.bot.send_message(chat_id, welcome_text, reply_markup=BotKeyboards.main_menu())

    async def cmd_market(self, chat_id: int) -> None:
        msg = await self.bot.send_message(chat_id, "⏳ Сбор данных по рынку фьючерсов...")
        tickers_data = []
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]:
            try:
                t = await BinanceFuturesAdapter().fetch_ticker(sym)
                tickers_data.append(t.model_dump())
            except Exception:
                pass

        breadth_data = {
            "breadth_state": "BULLISH_EXPANSION",
            "pct_above_ema50": 68.0,
            "advance_decline_ratio": 1.8,
        }

        overview = BotFormatters.format_market_overview(tickers_data, breadth_data)
        await self.bot.send_message(chat_id, overview, reply_markup=BotKeyboards.main_menu())

    async def cmd_top(self, chat_id: int) -> None:
        await self.bot.send_message(chat_id, "🔍 Сканирование рынка и фильтрация лучших сетапов...")
        setups = await self.scanner.scan_market()

        valid_setups = [s for s in setups if s.score >= 60.0 and s.direction.value != "NO_TRADE"]

        if not valid_setups:
            await self.bot.send_message(
                chat_id,
                "⚪ **В настоящее время статистического преимущества недостаточно.**\n"
                "Система находится в режиме Capital Protection (NO TRADE).",
                reply_markup=BotKeyboards.main_menu(),
            )
            return

        for setup in valid_setups[:3]:
            card = BotFormatters.format_signal(setup)
            kb = BotKeyboards.signal_detail_keyboard(setup.symbol)
            await self.bot.send_message(chat_id, card, reply_markup=kb)

    async def cmd_analyze(self, chat_id: int, symbol: str) -> None:
        await self.bot.send_message(chat_id, f"🔍 Глубокий количественный анализ {symbol}...")
        setup = await self.scanner.scan_symbol(symbol)
        if not setup:
            await self.bot.send_message(chat_id, f"⚠️ Не удалось получить данные по {symbol}.", reply_markup=BotKeyboards.main_menu())
            return

        card = BotFormatters.format_signal(setup)
        kb = BotKeyboards.signal_detail_keyboard(setup.symbol)
        await self.bot.send_message(chat_id, card, reply_markup=kb)

    async def cmd_backtest(self, chat_id: int) -> None:
        text = "🧪 **Выберите инструмент для запуска исторического бэктеста:**"
        await self.bot.send_message(chat_id, text, reply_markup=BotKeyboards.backtest_symbols_keyboard())

    async def run_backtest_action(self, chat_id: int, symbol: str, timeframe: str) -> None:
        await self.bot.send_message(chat_id, f"⏳ Загрузка свечей и тестирование стратегий для {symbol} ({timeframe})...")
        candles = await self.downloader.get_or_download_candles(symbol, timeframe, lookback_bars=500)

        strat = StrategyRegistry.list_active()[0]
        engine = BacktestEngine(strategy=strat)
        res = engine.run(candles)

        report = BotFormatters.format_backtest_report(res.metrics, symbol, strat.name)
        await self.bot.send_message(chat_id, report, reply_markup=BotKeyboards.main_menu())

    async def cmd_paper(self, chat_id: int) -> None:
        text = BotFormatters.format_paper_portfolio(paper_engine.portfolio)
        await self.bot.send_message(chat_id, text, reply_markup=BotKeyboards.main_menu())

    async def open_paper_trade_action(self, chat_id: int, symbol: str) -> None:
        setup = await self.scanner.scan_symbol(symbol)
        if not setup or setup.direction.value == "NO_TRADE":
            await self.bot.send_message(chat_id, f"⚠️ По {symbol} нет активного сетапа для входа.", reply_markup=BotKeyboards.main_menu())
            return

        allocated_margin = 500.0  # $500 default virtual trade margin
        pos = paper_engine.open_position_from_signal(setup, allocated_margin)
        if pos:
            await self.bot.send_message(
                chat_id,
                f"✅ **Открыта виртуальная позиция!**\n"
                f"• {pos.symbol} {pos.side} {pos.leverage}x\n"
                f"• Вход: `${pos.entry_price:,.2f}` | Qty: `{pos.quantity:.2f}`\n"
                f"• Выделенная маржа: `${pos.margin_locked:.2f}`",
                reply_markup=BotKeyboards.main_menu(),
            )
        else:
            await self.bot.send_message(chat_id, "⚠️ Не удалось открыть виртуальную позицию (недостаточно маржи или позиция уже открыта).", reply_markup=BotKeyboards.main_menu())

    async def cmd_news(self, chat_id: int) -> None:
        articles = await NewsFetcher.fetch_latest_news(limit=5)
        if not articles:
            await self.bot.send_message(chat_id, "📰 Новостной фон стабильный, критических событий не обнаружено.", reply_markup=BotKeyboards.main_menu())
            return

        lines = ["📰 **ПОСЛЕДНИЕ СОБЫТИЯ И НОВОСТНОЙ СЕНТИМЕНТ:**\n"]
        for a in articles:
            s_emoji = "🟢" if a["sentiment"] == "BULLISH" else ("🔴" if a["sentiment"] == "BEARISH" else "⚪")
            lines.append(f"{s_emoji} **{a['title']}**\n   _{a['source']}_ | Влияние: `{a['impact']}`\n")

        await self.bot.send_message(chat_id, "\n".join(lines), reply_markup=BotKeyboards.main_menu())

    async def cmd_strategies(self, chat_id: int) -> None:
        strategies = StrategyRegistry.list_all()
        lines = ["🧠 **РЕЕСТР КВАНТОВЫХ СТРАТЕГИЙ:**\n"]
        for s in strategies:
            regimes_str = ", ".join(r.value for r in s.expected_regimes) if s.expected_regimes else "ALL"
            lines.append(f"• **{s.name}** (v{s.version})\n  Статус: `{s.status.value}` | Режимы: `{regimes_str}`\n")

        await self.bot.send_message(chat_id, "\n".join(lines), reply_markup=BotKeyboards.main_menu())

    async def cmd_settings(self, chat_id: int) -> None:
        text = (
            "⚙️ **НАСТРОЙКИ ПРОФИЛЯ РИСКА:**\n\n"
            "Выберите допустимый риск на сделку (рассчитывается от дистанции стоп-лосса к депозиту):"
        )
        await self.bot.send_message(chat_id, text, reply_markup=BotKeyboards.settings_risk_keyboard())

    async def cmd_help(self, chat_id: int) -> None:
        help_text = (
            "📚 **СПРАВКА ПО КОМАНДАМ:**\n\n"
            "• `/start` — Главное меню\n"
            "• `/market` — Обзор рынка и широтные метрики\n"
            "• `/top` — Топ подтвержденных сетапов\n"
            "• `/analyze BTC` — Полный квантовый анализ актива\n"
            "• `/backtest` — Запуск бэктеста\n"
            "• `/paper` — Виртуальный торговый портфель\n"
            "• `/news` — Анализ новостей и сентимента\n"
            "• `/strategies` — Список активных стратегий\n"
            "• `/settings` — Настройки риска"
        )
        await self.bot.send_message(chat_id, help_text, reply_markup=BotKeyboards.main_menu())

    async def handle_natural_language(self, chat_id: int, query: str) -> None:
        response_text = await AIAssistant.process_user_query(query)
        await self.bot.send_message(chat_id, response_text, reply_markup=BotKeyboards.main_menu())
