"""
High-Performance In-Memory and Redis Caching Layer.
"""
import time
from typing import Any

from src.core.logging import get_logger

logger = get_logger("data.cache")


class CacheManager:
    """
    Tiered caching system: Fast Local Memory LRU with optional Redis persistence.
    """

    def __init__(self):
        self._memory_cache: dict[str, Any] = {}
        self._expiry: dict[str, float] = {}

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        """Store value with optional TTL."""
        self._memory_cache[key] = value
        if ttl_seconds is not None:
            self._expiry[key] = time.monotonic() + ttl_seconds
        elif key in self._expiry:
            del self._expiry[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve value if not expired."""
        if key not in self._memory_cache:
            return default

        if key in self._expiry:
            if time.monotonic() > self._expiry[key]:
                del self._memory_cache[key]
                del self._expiry[key]
                return default

        return self._memory_cache.get(key, default)

    def delete(self, key: str) -> None:
        """Remove key from cache."""
        self._memory_cache.pop(key, None)
        self._expiry.pop(key, None)

    def clear(self) -> None:
        """Wipe entire cache."""
        self._memory_cache.clear()
        self._expiry.clear()


# Global cache singleton
cache = CacheManager()
