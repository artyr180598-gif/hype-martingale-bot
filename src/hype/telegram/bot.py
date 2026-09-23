"""Telegram bot — aiogram 3.x, ported from v3 telegram.py but with ultimate features."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from loguru import logger

from ..analysis.engine import analyze_bundle
from ..config import Settings, build_line, load_config
from ..data.exchanges import ExchangeManager
from ..scanner.universe import build_universe
from ..signals.generator import analysis_to_signal
from ..store.db import SignalStore
from .keyboards import (
    alerts_kb,
    back_to_main_kb,
    coin_list_kb,
    main_menu_kb,
    settings_kb,
    signal_actions_kb,
)
from .render import (
    render_help,
    render_market_overview,
    render_no_access,
    render_scan_results,
    render_signal_card,
)


class HypeBot:
    def __init__(self, cfg: Settings, ex_manager: ExchangeManager, store: SignalStore):
        self.cfg = cfg
        self.ex_manager = ex_manager
        self.store = store
        self.bot = Bot(token=cfg.TELEGRAM_BOT_TOKEN) if cfg.TELEGRAM_BOT_TOKEN else None
        self.dp = Dispatcher()
        self._register_handlers()
        self.alerts_paused = False

    def _is_allowed(self, user_id: int) -> bool:
        if not self.cfg.allowed_user_ids:
            return False
        return user_id in self.cfg.allowed_user_ids

    def _register_handlers(self) -> None:
        dp = self.dp

        @dp.message(Command("start"))
        async def cmd_start(message: Message):
            if not self._is_allowed(message.from_user.id):
                await message.answer(render_no_access())
                return
            text = f"""
🚀 HYPE ULTIMATE v4 — мультибиржевой сканер

{build_line(self.cfg.APP_VERSION, self.cfg.APP_RELEASE)}

Что умеет:
• Сканит Binance, Bybit, OKX, MEXC, KuCoin, Gate, Bitget
• Находит монеты до пампа: EARLY/TRIGGERED фазы
• STOBB/SBM/JUMP детекторы (из CryptoScanBot)
• 11 стратегий голосуют за LONG/SHORT
• Считает вход, SL, 3 TP, R:R, плечо, ожидаемый скачок
• Анализ стакана: стены, дисбаланс, ликвидность

Жми кнопку ниже, чтобы начать 👇
"""
            await message.answer(text, reply_markup=main_menu_kb())

        @dp.message(Command("help"))
        async def cmd_help(message: Message):
            if not self._is_allowed(message.from_user.id):
                await message.answer(render_no_access())
                return
            await message.answer(render_help(), reply_markup=back_to_main_kb())

        @dp.message(Command("scan"))
        async def cmd_scan(message: Message):
            if not self._is_allowed(message.from_user.id):
                await message.answer(render_no_access())
                return
            await self._handle_scan(message)

        @dp.message(Command("signal"))
        async def cmd_signal(message: Message):
            if not self._is_allowed(message.from_user.id):
                await message.answer(render_no_access())
                return
            parts = message.text.split()
            symbol = parts[1].upper() if len(parts) > 1 else "BTCUSDT"
            await self._handle_signal(message, symbol, mode="beginner")

        @dp.message(Command("market"))
        async def cmd_market(message: Message):
            if not self._is_allowed(message.from_user.id):
                await message.answer(render_no_access())
                return
            await self._handle_market(message)

        @dp.callback_query(F.data == "main")
        async def cb_main(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            await callback.message.edit_text(
                f"🏠 Главное меню\n\n{build_line(self.cfg.APP_VERSION, self.cfg.APP_RELEASE)}",
                reply_markup=main_menu_kb(),
            )
            await callback.answer()

        @dp.callback_query(F.data == "scan")
        async def cb_scan(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            await callback.answer("🔎 Сканирую рынок...")
            # Send new message for scan (don't edit to preserve history)
            await self._handle_scan(callback.message, is_callback=True)

        @dp.callback_query(F.data.startswith("top_"))
        async def cb_top(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            direction = None
            if callback.data == "top_long":
                direction = "LONG"
            elif callback.data == "top_short":
                direction = "SHORT"
            await callback.answer(f"Загружаю топ {direction or 'все'}...")
            await self._handle_top(callback.message, direction=direction)

        @dp.callback_query(F.data == "top_all")
        async def cb_top_all(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            await callback.answer("Загружаю топ...")
            await self._handle_top(callback.message, direction=None)

        @dp.callback_query(F.data.startswith("signal:"))
        async def cb_signal(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            symbol = callback.data.split(":", 1)[1].upper()
            await callback.answer(f"Анализирую {symbol}...")
            await self._handle_signal(callback.message, symbol, mode="beginner", is_callback=True)

        @dp.callback_query(F.data.startswith("pro:"))
        async def cb_pro(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            symbol = callback.data.split(":", 1)[1].upper()
            await callback.answer(f"PRO разбор {symbol}...")
            await self._handle_signal(callback.message, symbol, mode="pro", is_callback=True)

        @dp.callback_query(F.data == "analyze_coin")
        async def cb_analyze_coin(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            # Show watchlist
            await callback.message.answer(
                "🔍 Выбери монету из списка или напиши символ (например BTCUSDT):",
                reply_markup=coin_list_kb(self.cfg.watchlist[:20]),
            )
            await callback.answer()

        @dp.callback_query(F.data == "market")
        async def cb_market(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            await callback.answer("Загружаю рынок...")
            await self._handle_market(callback.message, is_callback=True)

        @dp.callback_query(F.data == "alerts")
        async def cb_alerts(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            text = f"""
🔔 АВТО-СИГНАЛЫ

Статус: {'⏸ Пауза' if self.alerts_paused else '▶️ Активен'}
Интервал: {self.cfg.WATCHER_INTERVAL_SECONDS}с
Пороги:
• Quality ≥ {self.cfg.ALERT_MIN_QUALITY}
• Confidence ≥ {self.cfg.ALERT_MIN_BOT_CONFIDENCE}%
• Data ≥ {self.cfg.ALERT_MIN_DATA_CONFIDENCE}
• Risk ≤ {self.cfg.ALERT_MAX_RISK_SCORE}
• R:R ≥ {self.cfg.ALERT_MIN_RR}
• Max за цикл: {self.cfg.ALERT_MAX_PER_CYCLE}

Бот сам сканирует вселенную и пишет только сильные сетапы.
"""
            await callback.message.answer(text, reply_markup=alerts_kb(paused=self.alerts_paused))
            await callback.answer()

        @dp.callback_query(F.data.startswith("alerts:"))
        async def cb_alerts_action(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            action = callback.data.split(":")[1]
            if action == "toggle":
                self.alerts_paused = not self.alerts_paused
                await callback.answer(f"{'Пауза' if self.alerts_paused else 'Включен'}")
                await callback.message.edit_text(
                    f"🔔 Авто-сигналы: {'⏸ Пауза' if self.alerts_paused else '▶️ Активен'}",
                    reply_markup=alerts_kb(paused=self.alerts_paused),
                )
            elif action == "check":
                await callback.answer("Запускаю проверку...")
                await callback.message.answer("🔍 Запускаю внеплановую проверку...")
                # Triggered via watcher, but we can simulate
                await self._handle_scan(callback.message, is_callback=True)

        @dp.callback_query(F.data == "settings")
        async def cb_settings(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            user_settings = self.store.get_user_settings(callback.from_user.id)
            text = f"""
⚙️ Настройки

Режим: {user_settings.get('mode')}
Депозит: ${user_settings.get('deposit')}
Риск на сделку: {user_settings.get('risk_pct')}%

{build_line(self.cfg.APP_VERSION, self.cfg.APP_RELEASE)}
"""
            await callback.message.answer(text, reply_markup=settings_kb(current_mode=user_settings.get("mode", "beginner")))
            await callback.answer()

        @dp.callback_query(F.data.startswith("settings:"))
        async def cb_settings_action(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            parts = callback.data.split(":")
            if len(parts) >= 3 and parts[1] == "mode":
                mode = parts[2]
                self.store.set_user_settings(callback.from_user.id, mode=mode)
                await callback.answer(f"Режим {mode}")
                await callback.message.edit_text(f"⚙️ Режим изменен на {mode}", reply_markup=settings_kb(current_mode=mode))
            else:
                await callback.answer("В разработке — используй /start")

        @dp.callback_query(F.data == "help")
        async def cb_help(callback: CallbackQuery):
            if not self._is_allowed(callback.from_user.id):
                await callback.answer("⛔ Нет доступа", show_alert=True)
                return
            await callback.message.answer(render_help(), reply_markup=back_to_main_kb())
            await callback.answer()

        # Catch all text as symbol
        @dp.message(F.text)
        async def handle_text(message: Message):
            if not self._is_allowed(message.from_user.id):
                await message.answer(render_no_access())
                return
            text = message.text.strip().upper()
            # If looks like symbol
            if len(text) >= 4 and len(text) <= 15 and text.isalnum():
                # Add USDT if not present and looks like coin
                if not text.endswith("USDT") and not text.endswith("PERP"):
                    # Try with USDT
                    candidate = text + "USDT"
                else:
                    candidate = text
                await self._handle_signal(message, candidate, mode="beginner")
            else:
                await message.answer("Не понял. Напиши символ монеты, например BTCUSDT или выбери из меню.", reply_markup=main_menu_kb())

    async def _handle_scan(self, message: Message, is_callback: bool = False):
        await message.answer("🔎 Сканирую рынок — это займет 20-40 секунд...")

        try:
            universe = await build_universe(self.ex_manager, self.cfg, max_symbols=self.cfg.SCAN_LIMIT)
            if not universe:
                await message.answer("⚠️ Нет данных вселенной — биржи недоступны. Попробуй позже.", reply_markup=back_to_main_kb())
                return

            # Stage1 heat for top pool
            # For speed, analyze top SCAN_TOP + emergence pool
            candidates = universe[: self.cfg.SCAN_EMERGENCE_POOL]

            # Analyze each
            signals = []
            for ticker in candidates[: self.cfg.SCAN_TOP + 10]:
                try:
                    bundle = await self.ex_manager.get_bundle(
                        ticker.symbol, timeframes=self.cfg.timeframes, preferred_exchange=self.cfg.PRIMARY_EXCHANGE
                    )
                    if not bundle.has_minimum:
                        continue
                    analysis = analyze_bundle(bundle, self.cfg)
                    sig = analysis_to_signal(analysis, self.cfg)
                    if sig and sig.status == "SIGNAL" and sig.quality_score >= self.cfg.SCAN_LIST_QUALITY_MIN:
                        signals.append(sig)
                        self.store.save_signal(sig)
                except Exception as e:
                    logger.debug(f"Scan {ticker.symbol} failed: {e}")
                    continue

            # Sort by quality * confidence
            signals.sort(key=lambda s: s.quality_score * 0.6 + s.confidence_pct * 0.4, reverse=True)

            # Render
            text = render_scan_results(signals, title="🔎 Скан рынка")
            await message.answer(text, reply_markup=coin_list_kb([s.symbol for s in signals[:15]]) if signals else back_to_main_kb())

        except Exception as e:
            logger.error(f"Scan failed: {e}")
            await message.answer(f"⚠️ Ошибка скана: {e}", reply_markup=back_to_main_kb())

    async def _handle_top(self, message: Message, direction: str | None = None):
        try:
            # Get from store
            top = self.store.get_top(direction=direction, limit=20)
            if not top:
                await message.answer("📭 Пока нет сохраненных сигналов. Запусти скан.", reply_markup=back_to_main_kb())
                return

            # Convert to Signal-like for render
            # Quick render from DB rows
            lines = [f"{'🔥 LONG' if direction=='LONG' else '🔻 SHORT' if direction else '⭐ Топ'} — {len(top)} сигналов", ""]
            for i, row in enumerate(top[:15], 1):
                dir_emoji = "🟢" if row["direction"] == "LONG" else "🔴"
                lines.append(
                    f"{i}. {dir_emoji} {row['symbol']} {row['direction']} | {row['quality_grade']} {row['quality_score']:.0f} | conf {row['confidence_pct']:.0f}% | RR 1:{row['risk_reward']:.1f}"
                )
            text = "\n".join(lines)
            symbols = [r["symbol"] for r in top[:15]]
            await message.answer(text, reply_markup=coin_list_kb(symbols))

        except Exception as e:
            await message.answer(f"⚠️ Ошибка загрузки топа: {e}", reply_markup=back_to_main_kb())

    async def _handle_signal(self, message: Message, symbol: str, mode: str = "beginner", is_callback: bool = False):
        try:
            bundle = await self.ex_manager.get_bundle(
                symbol, timeframes=self.cfg.timeframes, preferred_exchange=self.cfg.PRIMARY_EXCHANGE
            )
            if not bundle.has_minimum:
                await message.answer(
                    f"⚠️ Нет данных по {symbol}\nПопробуй другой символ или позже.\nОшибки: {', '.join(bundle.errors[:3])}",
                    reply_markup=back_to_main_kb(),
                )
                return

            analysis = analyze_bundle(bundle, self.cfg)
            sig = analysis_to_signal(analysis, self.cfg)

            if not sig:
                await message.answer(f"⚠️ Не удалось проанализировать {symbol}", reply_markup=back_to_main_kb())
                return

            # Save
            self.store.save_signal(sig)

            text = render_signal_card(sig, mode=mode, version=self.cfg.APP_VERSION, release=self.cfg.APP_RELEASE)
            await message.answer(text, reply_markup=signal_actions_kb(symbol))

        except Exception as e:
            logger.error(f"Signal {symbol} failed: {e}")
            await message.answer(f"⚠️ Ошибка анализа {symbol}: {e}", reply_markup=back_to_main_kb())

    async def _handle_market(self, message: Message, is_callback: bool = False):
        try:
            # Fetch BTC and ETH
            btc_bundle = await self.ex_manager.get_bundle("BTCUSDT", timeframes=["1h"], preferred_exchange=self.cfg.PRIMARY_EXCHANGE)
            eth_bundle = await self.ex_manager.get_bundle("ETHUSDT", timeframes=["1h"], preferred_exchange=self.cfg.PRIMARY_EXCHANGE)

            btc_price = btc_bundle.ticker.last if btc_bundle.ticker else None
            eth_price = eth_bundle.ticker.last if eth_bundle.ticker else None

            # Universe for gainers
            universe = await build_universe(self.ex_manager, self.cfg, max_symbols=50)
            gainers = []
            for t in universe:
                if t.change_24h_pct is not None:
                    gainers.append({"symbol": t.symbol, "change": t.change_24h_pct})
            gainers.sort(key=lambda x: x["change"], reverse=True)

            text = render_market_overview(btc_price=btc_price, eth_price=eth_price, gainers=gainers[:10])
            await message.answer(text, reply_markup=back_to_main_kb())

        except Exception as e:
            await message.answer(f"⚠️ Ошибка рынка: {e}", reply_markup=back_to_main_kb())

    async def start(self) -> None:
        if not self.bot:
            logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram disabled")
            return
        logger.info(f"Starting Telegram bot {build_line(self.cfg.APP_VERSION, self.cfg.APP_RELEASE)}")
        await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        if self.bot:
            await self.bot.session.close()
