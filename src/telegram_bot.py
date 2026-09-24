import asyncio
import logging
import os

import aiohttp

from src.strategies.pump_scanner import PumpScanner

log = logging.getLogger(__name__)


class TelegramBot:
    """Russian Telegram UI for the Bybit Pump/Dump scanner."""

    def __init__(self, hub=None):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.offset = 0
        self.running = False
        self.session = None
        self.task = None
        self.monitor_task = None
        self.scanner = PumpScanner()
        self.menu_keyboard = {
            "keyboard": [
                [{"text": "🔎 Сканировать сейчас"}, {"text": "⚙️ Настройки"}],
                [{"text": "🟢 Только Pump"}, {"text": "🔴 Только Dump"}],
                [{"text": "🟢🔴 Pump + Dump"}, {"text": "❤️ Проверка"}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
        }

    async def _api(self, method, payload=None):
        if not self.session:
            raise RuntimeError("Telegram session is not started")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        async with self.session.post(url, json=payload or {},
                                     timeout=aiohttp.ClientTimeout(total=35)) as response:
            data = await response.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API: {data.get('description', 'unknown error')}")
            return data.get("result")

    async def start(self):
        if not self.token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN and TELEGRAM_CHAT_ID are required")
        self.session = aiohttp.ClientSession()
        await self.scanner.start()
        self.running = True
        await self._api("deleteWebhook", {"drop_pending_updates": False})
        await self._api("setMyCommands", {"commands": [
            {"command": "start", "description": "Открыть меню"},
            {"command": "scan", "description": "Сканировать Bybit сейчас"},
            {"command": "settings", "description": "Показать настройки"},
            {"command": "health", "description": "Проверить соединение и рынок"},
        ]})
        await self._send(
            self.chat_id,
            "🚀 Bybit Pump/Dump Scanner\n\n"
            "Автоматический мониторинг запущен.\n"
            "Бот сам ищет сильные движения по всему доступному USDT-фьючерсному рынку Bybit.\n\n"
            "Важно: Pump/Dump — это обнаружение движения, а ЛОНГ/ШОРТ — отдельное подтверждение. "
            "Если подтверждения нет, бот прямо пишет «ЖДАТЬ».",
            keyboard=True,
        )
        self.task = asyncio.create_task(self._poll(), name="telegram-poll")
        self.monitor_task = asyncio.create_task(self._monitor_loop(), name="pump-monitor")

    async def stop(self):
        self.running = False
        for task in (self.task, self.monitor_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await self.scanner.stop()
        if self.session and not self.session.closed:
            await self.session.close()

    async def _monitor_loop(self):
        while self.running:
            try:
                signals = await self.scanner.update()
                for signal in signals:
                    await self._send(self.chat_id, self.scanner.format_signal(signal))
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Pump monitor cycle failed")
            await asyncio.sleep(2)

    async def _poll(self):
        while self.running:
            try:
                updates = await self._api("getUpdates", {
                    "offset": self.offset,
                    "timeout": 25,
                    "allowed_updates": ["message"],
                })
                for update in updates or []:
                    self.offset = int(update["update_id"]) + 1
                    await self._handle(update)
            except asyncio.CancelledError:
                return
            except Exception:
                log.warning("Telegram poll failed", exc_info=True)
                await asyncio.sleep(3)

    def _allowed(self, update):
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        return str(chat.get("id", "")) == str(self.chat_id)

    async def _send(self, chat_id, text, keyboard=False):
        payload = {"chat_id": chat_id, "text": text}
        if keyboard:
            payload["reply_markup"] = self.menu_keyboard
        await self._api("sendMessage", payload)

    async def _handle(self, update):
        if not self._allowed(update):
            return
        message = update["message"]
        chat_id = message["chat"]["id"]
        command = (message.get("text") or "").strip()

        aliases = {
            "🔎 Сканировать сейчас": "scan",
            "⚙️ Настройки": "settings",
            "🟢 Только Pump": "pump",
            "🔴 Только Dump": "dump",
            "🟢🔴 Pump + Dump": "both",
            "❤️ Проверка": "health",
        }
        command = aliases.get(command, command).lower()

        if command.startswith("/start") or command in {"menu", "/menu"}:
            await self._send(chat_id, "🚀 Меню сканера Bybit", keyboard=True)
        elif command in {"scan", "/scan", "pump", "dump", "both"}:
            if command == "pump":
                self.scanner.settings.signal_types = "PUMP"
            elif command == "dump":
                self.scanner.settings.signal_types = "DUMP"
            elif command == "both":
                self.scanner.settings.signal_types = "BOTH"
            self.scanner.settings.save()
            await self._manual_scan(chat_id)
        elif command in {"settings", "/settings"}:
            await self._settings(chat_id)
        elif command in {"health", "/health"}:
            await self._health(chat_id)
        else:
            await self._send(chat_id, "Используй кнопки меню.", keyboard=True)

    async def _manual_scan(self, chat_id):
        await self._send(chat_id, "🔎 Проверяю рынок Bybit...")
        try:
            signals = await asyncio.wait_for(self.scanner.scan_once(), timeout=30)
        except asyncio.TimeoutError:
            await self._send(chat_id, "⚠️ Проверка не завершилась за 30 секунд. Сигнал не придумываю.")
            return
        if not signals:
            await self._send(chat_id, "Сейчас нет нового движения, прошедшего выбранные фильтры.")
            return
        for signal in signals[:10]:
            await self._send(chat_id, self.scanner.format_signal(signal))

    async def _settings(self, chat_id):
        s = self.scanner.settings
        tfs = ", ".join(s.rsi_timeframes)
        text = (
            "⚙️ Настройки\n\n"
            "1. Интервал мониторинга: "
            f"{self._interval_label(s.interval_seconds)}\n"
            f"2. Порог изменения цены: {s.threshold_pct:.2f}%\n"
            f"3. RSI: {'ВКЛ' if s.rsi_enabled else 'ВЫКЛ'} "
            f"({tfs}), уровни {s.rsi_overbought:.0f}/{s.rsi_oversold:.0f}\n"
            f"4. Фильтр 24ч: {'ВКЛ' if s.day_filter_enabled else 'ВЫКЛ'} "
            f"({s.day_min_pct:.1f}%)\n"
            f"5. Типы сигналов: {s.signal_types}\n\n"
            "Дополнительные данные:\n"
            f"• Стакан: {'ВКЛ' if s.show_imbalance else 'ВЫКЛ'}\n"
            f"• Объём 24ч: {'ВКЛ' if s.show_volume else 'ВЫКЛ'}\n"
            f"• Всплеск объёма: {'ВКЛ' if s.show_volume_spike else 'ВЫКЛ'}\n"
            f"• Open Interest: {'ВКЛ' if s.show_oi else 'ВЫКЛ'}\n"
            f"• Funding: {'ВКЛ' if s.show_funding else 'ВЫКЛ'}\n"
            f"• Дата листинга: {'ВКЛ' if s.show_listing else 'ВЫКЛ'}\n\n"
            "Первые два фильтра являются базовыми и не отключаются."
        )
        await self._send(chat_id, text, keyboard=True)

    async def _health(self, chat_id):
        try:
            tickers = await self.scanner.fetch_tickers()
            ws = len(self.scanner.ticker_cache)
            await self._send(
                chat_id,
                "❤️ Состояние сканера: LIVE\n"
                f"Инструментов Bybit: {len(self.scanner.ws_symbols)}\n"
                f"Получено ticker-данных: {ws}\n"
                f"REST ticker сейчас: {len(tickers)}\n"
                f"Порог: {self.scanner.settings.threshold_pct:.2f}%\n"
                f"Интервал: {self._interval_label(self.scanner.settings.interval_seconds)}",
            )
        except Exception as exc:
            await self._send(chat_id, f"❌ Ошибка получения данных Bybit: {type(exc).__name__}")

    @staticmethod
    def _interval_label(seconds):
        if seconds < 60:
            return f"{seconds} сек"
        return f"{seconds // 60} мин"
