"""
Мониторинг ошибок и супервизор фоновых задач.

Задачи:
  1. ErrorMonitor — копилка ошибок с окном во времени: сколько раз и какой
     провайдер падал. Сканер/репортёр смотрят сюда и честно пишут пользователю
     «данные по холдерам недоступны», а не молча выдают ноль.
  2. HealthRegistry — метрики живости (последний успешный скан, число
     кандидатов, аптайм) для /health и для Telegram-команды /status.
  3. supervise() — обёртка над asyncio-задачей: перезапускает её с
     экспоненциальной задержкой, поэтому упавший WebSocket-поток или сканер
     не роняет процесс целиком (главное требование «бот не падает при сбоях API»).
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from v2.core.logging import get_logger

logger = get_logger("core.monitor")


@dataclass
class ErrorRecord:
    ts: float
    component: str
    kind: str
    message: str
    fatal: bool = False


@dataclass
class ErrorMonitor:
    """Скользящее окно ошибок + счётчики по компонентам."""

    window_sec: float = 900.0
    max_records: int = 500
    records: deque[ErrorRecord] = field(default_factory=lambda: deque(maxlen=500))
    counters: Counter[str] = field(default_factory=Counter)
    total: int = 0

    def record(self, component: str, exc: BaseException | str, fatal: bool = False) -> ErrorRecord:
        kind = type(exc).__name__ if isinstance(exc, BaseException) else "message"
        message = str(exc)[:400]
        rec = ErrorRecord(ts=time.time(), component=component, kind=kind, message=message, fatal=fatal)
        self.records.append(rec)
        self.counters[f"{component}:{kind}"] += 1
        self.total += 1
        log = logger.error if fatal else logger.warning
        log("%s: %s: %s", component, kind, message)
        return rec

    def _prune(self) -> None:
        cutoff = time.time() - self.window_sec
        while self.records and self.records[0].ts < cutoff:
            self.records.popleft()

    def recent(self, component: str | None = None, limit: int = 20) -> list[ErrorRecord]:
        self._prune()
        items = [r for r in self.records if component is None or r.component == component]
        return list(items)[-limit:]

    def is_degraded(self, component: str, threshold: int = 3) -> bool:
        """Компонент считается деградировавшим, если падал ≥ threshold раз за окно."""
        self._prune()
        hits = sum(1 for r in self.records if r.component == component)
        return hits >= threshold

    def snapshot(self) -> dict[str, Any]:
        self._prune()
        return {
            "total_errors": self.total,
            "errors_in_window": len(self.records),
            "by_component": dict(self.counters.most_common(10)),
            "last": [
                {"component": r.component, "kind": r.kind, "message": r.message, "age_sec": round(time.time() - r.ts)}
                for r in list(self.records)[-5:]
            ],
        }


@dataclass
class HealthRegistry:
    """Простые метрики живости процесса."""

    started_at: float = field(default_factory=time.time)
    state: dict[str, Any] = field(default_factory=dict)
    last_ok: dict[str, float] = field(default_factory=dict)

    def mark_ok(self, name: str, **extra: Any) -> None:
        self.last_ok[name] = time.time()
        if extra:
            self.state.update({f"{name}.{k}": v for k, v in extra.items()})

    def mark(self, key: str, value: Any) -> None:
        self.state[key] = value

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "uptime_sec": round(now - self.started_at, 1),
            "state": dict(self.state),
            "last_ok_age_sec": {k: round(now - v, 1) for k, v in self.last_ok.items()},
        }


# Глобальные экземпляры: их переиспользуют все модули, а тесты подменяют.
monitor = ErrorMonitor()
health = HealthRegistry()


async def supervise(
    name: str,
    factory: Callable[[], Awaitable[Any]],
    *,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    stop_event: asyncio.Event | None = None,
    max_restarts: int | None = None,
) -> None:
    """
    Бесконечно крутит корутину ``factory()``, перезапуская после падения.

    Задержка растёт экспоненциально (1 → 2 → 4 → … → max_delay) и сбрасывается,
    если задача проработала дольше 60 секунд. ``max_restarts`` нужен тестам,
    чтобы не крутиться вечно.
    """
    delay = base_delay
    restarts = 0
    while stop_event is None or not stop_event.is_set():
        started = time.time()
        try:
            await factory()
            # Корутина завершилась штатно — тоже перезапускаем, но без бэкоффа.
            delay = base_delay
        except asyncio.CancelledError:
            logger.info("%s: отменён", name)
            raise
        except Exception as exc:  # noqa: BLE001 — супервизор обязан выжить
            monitor.record(f"supervisor.{name}", exc, fatal=False)
            health.mark(f"{name}.last_error", str(exc)[:200])
        if time.time() - started > 60:
            delay = base_delay
        restarts += 1
        if max_restarts is not None and restarts > max_restarts:
            logger.error("%s: превышен лимит перезапусков (%d)", name, max_restarts)
            return
        logger.warning("%s: перезапуск через %.1fс (попытка %d)", name, delay, restarts)
        health.mark(f"{name}.restarts", restarts)
        try:
            await asyncio.wait_for(stop_event.wait() if stop_event else asyncio.sleep(delay), timeout=delay)
            if stop_event is not None and stop_event.is_set():
                return
        except asyncio.TimeoutError:
            pass
        delay = min(delay * 2, max_delay)
