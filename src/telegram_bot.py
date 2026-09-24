import os
import aiohttp

class TelegramBot:
    def __init__(self, hub):
        self.hub = hub
        self.token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.session = None

    async def start(self):
        if not self.token or not self.chat_id:
            raise RuntimeError('TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required')
        self.session = aiohttp.ClientSession()

    async def stop(self):
        if self.session and not self.session.closed:
            await self.session.close()
