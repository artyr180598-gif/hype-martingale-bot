"""
Alert Dispatcher with Event Deduplication and Cooldown Controls.
"""
import hashlib
import time

from src.config.settings import settings
from src.core.logging import get_logger
from src.signals.models import SignalSetup

logger = get_logger("alerts.dispatcher")


class AlertDispatcher:
    """
    Manages outbound notifications, deduplication hashes, and anti-spam limits.
    """

    def __init__(self, cooldown_seconds: float = 3600.0):
        self.cooldown_seconds = cooldown_seconds
        self._sent_event_hashes: dict[str, float] = {}

    def generate_event_hash(self, symbol: str, alert_type: str, direction: str) -> str:
        raw = f"{symbol}:{alert_type}:{direction}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def should_dispatch_alert(self, setup: SignalSetup) -> bool:
        if setup.score < settings.TELEGRAM_ALERT_MIN_SCORE:
            return False

        if setup.direction.value == "NO_TRADE":
            return False

        event_hash = self.generate_event_hash(setup.symbol, "HIGH_CONVICTION_SETUP", setup.direction.value)
        now = time.monotonic()

        if event_hash in self._sent_event_hashes:
            last_sent = self._sent_event_hashes[event_hash]
            if now - last_sent < self.cooldown_seconds:
                return False  # Suppress duplicate alert within cooldown

        self._sent_event_hashes[event_hash] = now
        return True
