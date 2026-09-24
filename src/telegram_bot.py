import asyncio
import logging
import os

import aiohttp

from src.strategies.pump_scanner import PumpScanner

log = logging.getLogger(__name__)


class TelegramBot:
    """Telegram UI for the Bybit Pump/Dump scanner."""

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
        async with self.session.post(
            url,
            json=payload or {},
            timeout=aiohttp.ClientTimeout(total=35),
        ) as response:
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
        await self._api(
            "setMyCommands",
            {
                "commands": [
                    {"command": "start", "description": "Открыть меню"},
                    {"command": "scan", "description": "Сканировать Bybit сейчас"},
                    {"command": "settings", "description": "Настройки"},
                    {"command": "health", "description": "Состояние сканера"},
                ]
            },
        )
        await self._send(
            self.chat_id,
            "🚀 Bybit Pump/Dump Scanner\n\n"
            "Мониторинг запущен. Бот сам ищет сильные движения по USDT-фьючерсам Bybit.\n\n"
            "Важно: Pump/Dump — обнаружение движения. ЛОНГ/ШОРТ появляется "
            "только после отдельной проверки продолжения движения. "
            "Если подтверждения нет, бот пишет «ЖДАТЬ».",
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
                updates = await self._api(
                    "getUpdates",
                    {
                        "offset": self.offset,
                        "timeout": 25,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                for update in updates or []:
                    self.offset = int(update["update_id"]) + 1
                    if update.get("callback_query"):
                        await self._handle_callback(update["callback_query"])
                    elif update.get("message"):
                        await self._handle_message(update["message"])
            except asyncio.CancelledError:
                return
            except Exception:
                log.warning("Telegram poll failed", exc_info=True)
                await asyncio.sleep(3)

    def _allowed_chat(self, chat_id):
        return str(chat_id) == str(self.chat_id)

    async def _handle_message(self, message):
        chat_id = message["chat"]["id"]
        if not self._allowed_chat(chat_id):
            return

        text = (message.get("text") or "").strip()
        aliases = {
            "🔎 Сканировать сейчас": "scan",
            "⚙️ Настройки": "settings",
            "🟢 Только Pump": "pump",
            "🔴 Только Dump": "dump",
            "🟢🔴 Pump + Dump": "both",
            "❤️ Проверка": "health",
        }
        command = aliases.get(text, text).lower()

        if command.startswith("/start") or command in {"menu", "/menu"}:
            await self._send(chat_id, "🚀 Меню сканера Bybit", keyboard=True)
        elif command in {"scan", "/scan"}:
            await self._manual_scan(chat_id)
        elif command in {"settings", "/settings"}:
            await self._settings(chat_id)
        elif command in {"pump", "dump", "both"}:
            self.scanner.settings.signal_types = {
                "pump": "PUMP",
                "dump": "DUMP",
                "both": "BOTH",
            }[command]
            self.scanner.settings.save()
            await self._send(chat_id, f"Тип сигналов: {self.scanner.settings.signal_types}", keyboard=True)
        elif command in {"health", "/health"}:
            await self._health(chat_id)
        else:
            await self._send(chat_id, "Используй кнопки меню.", keyboard=True)

    async def _handle_callback(self, query):
        callback_id = query.get("id")
        data = query.get("data", "")
        message = query.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if callback_id:
            try:
                await self._api("answerCallbackQuery", {"callback_query_id": callback_id})
            except Exception:
                pass
        if not self._allowed_chat(chat_id):
            return

        if data == "settings":
            await self._settings(chat_id)
        elif data.startswith("interval:"):
            self.scanner.settings.interval_seconds = int(data.split(":", 1)[1])
            self.scanner.settings.save()
            await self._settings(chat_id)
        elif data.startswith("threshold:"):
            self.scanner.settings.threshold_pct = float(data.split(":", 1)[1])
            self.scanner.settings.save()
            await self._settings(chat_id)
        elif data.startswith("signals:"):
            self.scanner.settings.signal_types = data.split(":", 1)[1]
            self.scanner.settings.save()
            await self._settings(chat_id)
        elif data.startswith("rsi:"):
            value = data.split(":", 1)[1]
            self.scanner.settings.rsi_enabled = value == "on"
            self.scanner.settings.save()
            await self._settings(chat_id)
        elif data.startswith("daypct:"):
            value = float(data.split(":", 1)[1])
            self.scanner.settings.day_filter_enabled = value > 0
            self.scanner.settings.day_min_pct = value
            self.scanner.settings.save()
            await self._settings(chat_id)
        elif data.startswith("day:"):
            value = data.split(":", 1)[1]
            self.scanner.settings.day_filter_enabled = value == "on"
            self.scanner.settings.save()
            await self._settings(chat_id)
        elif data == "noop":
            return
        elif data == "back":
            await self._send(chat_id, "🚀 Меню сканера Bybit", keyboard=True)

    async def _send(self, chat_id, text, keyboard=False, inline=None):
        payload = {"chat_id": chat_id, "text": text}
        if keyboard:
            payload["reply_markup"] = self.menu_keyboard
        if inline:
            payload["reply_markup"] = {"inline_keyboard": inline}
        await self._api("sendMessage", payload)

    async def _manual_scan(self, chat_id):
        await self._send(chat_id, "🔎 Проверяю рынок Bybit...")