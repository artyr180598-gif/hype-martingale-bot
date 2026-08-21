from collections.abc import AsyncIterator
from typing import Protocol

from app.data.models import MarketEvent


class MarketDataAdapter(Protocol):
    name: str

    async def stream(self, symbols: list[str]) -> AsyncIterator[MarketEvent]:
        """Yield normalized market events until the stream is cancelled."""
        ...

    async def close(self) -> None:
        """Release network resources."""
        ...
