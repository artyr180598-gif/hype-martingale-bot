"""
TTL-кэш с защитой от «толпы» (single-flight).

Зачем: DexScreener и GoPlus отдают данные, которые не меняются быстрее раза в
30–60 секунд, а сканер может спросить один и тот же токен из трёх уровней.
Кэш режет число запросов в разы и спасает от 429.

Single-flight: если 10 корутин одновременно запросили один ключ, в сеть уйдёт
ОДИН запрос, остальные дождутся его результат через asyncio.Future.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from v2.core.logging import get_logger

logger = get_logger("core.cache")


@dataclass
class _Entry:
    value: Any
    expires_at: float


@dataclass
class TtlCache:
    ttl_sec: float = 60.0
    max_size: int = 4096
    name: str = "cache"
    _data: dict[str, _Entry] = field(default_factory=dict)
    _inflight: dict[str, asyncio.Future] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.time():
            self._data.pop(key, None)
            return None
        self.hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        if len(self._data) >= self.max_size:
            # простейшая эвакуация: выкидываем самое старое по expires_at
            oldest = min(self._data.items(), key=lambda kv: kv[1].expires_at)[0]
            self._data.pop(oldest, None)
        self._data[key] = _Entry(value=value, expires_at=time.time() + (ttl if ttl is not None else self.ttl_sec))

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()
        self._inflight.clear()

    async def get_or_load(self, key: str, loader: Callable[[], Awaitable[Any]], ttl: float | None = None) -> Any:
        """Возвращает значение из кэша либо грузит его ровно один раз."""
        cached = self.get(key)
        if cached is not None:
            return cached

        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut
        self.misses += 1
        try:
            value = await loader()
            if value is not None:
                self.set(key, value, ttl)
            fut.set_result(value)
            return value
        except BaseException as exc:
            fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": len(self._data), "inflight": len(self._inflight)}
