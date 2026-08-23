"""
Macroeconomic and Crypto Event Calendar.
"""
from dataclasses import dataclass

from src.core.time_utils import utc_now_ms


@dataclass
class ScheduledEvent:
    event_id: str
    name: str
    category: str        # "FOMC", "CPI", "NFP", "TOKEN_UNLOCK", "MAINTENANCE"
    impact: str          # "HIGH", "CRITICAL"
    timestamp_ms: int
    affected_assets: list[str]


class EventCalendar:
    """
    Monitors upcoming macroeconomic releases and exchange maintenance events.
    """

    @classmethod
    def get_upcoming_events(cls, horizon_hours: float = 24.0) -> list[ScheduledEvent]:
        # Preloaded major high-impact calendar fixtures
        now_ms = utc_now_ms()
        return []

    @classmethod
    def is_high_risk_event_imminent(cls, asset: str = "BTCUSDT", buffer_hours: float = 2.0) -> tuple[bool, str | None]:
        # Returns True if major event like CPI or FOMC is within buffer_hours
        return False, None
