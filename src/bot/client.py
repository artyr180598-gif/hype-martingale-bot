"""
Asynchronous Telegram Bot API Client.
"""
from typing import Any

import aiohttp

from src.config.settings import settings
from src.core.logging import get_logger

logger = get_logger("bot.client")


class TelegramBotClient:
    """
    Direct asynchronous client for Telegram Bot API.
    """

    BASE_URL = "https://api.telegram.org"

    def __init__(self, token: str | None = None):
        self.token = token or settings.TELEGRAM_BOT_TOKEN
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30.0)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str = "Markdown",
    ) -> dict[str, Any] | None:
        if not self.token:
            logger.debug("Telegram token missing; logging message locally", chat_id=chat_id, text=text[:80])
            return None

        url = f"{self.BASE_URL}/bot{self.token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    # Retry without markdown if formatting was invalid
                    if parse_mode:
                        payload.pop("parse_mode")
                        async with session.post(url, json=payload) as retry_resp:
                            return await retry_resp.json()
                return await resp.json()
        except Exception as e:
            logger.error("Failed to send Telegram message", chat_id=chat_id, error=str(e))
            return None

    async def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str = "Markdown",
    ) -> dict[str, Any] | None:
        if not self.token:
            return None

        url = f"{self.BASE_URL}/bot{self.token}/editMessageText"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("Failed to edit Telegram message", error=str(e))
            return None

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        if not self.token:
            return

        url = f"{self.BASE_URL}/bot{self.token}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text

        try:
            session = await self._get_session()
            await session.post(url, json=payload)
        except Exception as e:
            logger.debug("Failed to answer callback query", error=str(e))

    async def get_updates(self, offset: int | None = None, timeout: int = 20) -> list[dict[str, Any]]:
        if not self.token:
            return []

        url = f"{self.BASE_URL}/bot{self.token}/getUpdates"
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset

        try:
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", [])
        except Exception as e:
            logger.debug("get_updates poll error", error=str(e))

        return []

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
