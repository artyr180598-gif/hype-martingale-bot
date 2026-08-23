"""
Telegram Bot Long-Polling and Webhook Server Runner.
"""
import asyncio

from src.bot.client import TelegramBotClient
from src.bot.handlers import BotHandlers
from src.config.settings import settings
from src.core.logging import get_logger

logger = get_logger("bot.server")


class TelegramBotRunner:
    """
    Manages the lifecycle of Telegram long-polling.
    """

    def __init__(self):
        self.bot = TelegramBotClient()
        self.handlers = BotHandlers(self.bot)
        self._running = False

    async def run_polling(self) -> None:
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN not configured. Bot polling will idle.")
            return

        self._running = True
        offset = 0
        logger.info("Starting Telegram Bot long-polling...")

        while self._running:
            try:
                updates = await self.bot.get_updates(offset=offset, timeout=15)
                for u in updates:
                    offset = u.get("update_id", 0) + 1

                    if "message" in u:
                        await self.handlers.handle_message(u["message"])
                    elif "callback_query" in u:
                        await self.handlers.handle_callback_query(u["callback_query"])

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in Telegram poll loop", error=str(e))
                await asyncio.sleep(3.0)

    async def stop(self) -> None:
        self._running = False
        await self.bot.close()
