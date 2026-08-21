import asyncio

from core.config import get_settings
from telegram_bot.bot import run


if __name__ == "__main__":
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    asyncio.run(run(settings.telegram_bot_token))
