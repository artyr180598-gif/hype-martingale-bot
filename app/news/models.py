from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class NewsSentiment(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class Impact(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class NewsEvent:
    event_id: str
    published_at: datetime
    source: str
    headline: str
    assets: tuple[str, ...]
    sentiment: NewsSentiment
    impact: Impact
    confidence: float

    @property
    def available_at(self) -> datetime:
        return self.published_at
