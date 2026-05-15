import requests
import logging
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    def send(self, text: str):
        try:
            requests.post(
                self.url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                timeout=10
            )
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
