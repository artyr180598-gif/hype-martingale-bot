"""
Публичный WebSocket-поток принудительных ликвидаций Bybit v5 (real-time).

У Bybit нет REST-фида ликвидаций для USDT-perp — только WebSocket-топики
``liquidation.<SYMBOL>``. Этот модуль — единственный честный способ получить
реальные ликвидации без подмены их «прокси крупных сделок»:

  * один постоянный коллектор на процесс (запускает ``v3.data``);
  * реконнект с экспоненциальным backoff, app-level ping каждые 20 с;
  * кольцевой буфер событий с биржевыми timestamp (``updatedTime``);
  * если поток недоступен/нездоров — потребители получают пустой список и
    честно показывают «н/д» вместо синтетических или прокси-данных.

Парсинг ``parse_liquidation_message`` и буфер ``LiquidationBuffer`` — чистые
функции/структуры без сети: полностью покрываются юнит-тестами.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Callable

from src.core.logging import get_logger
from src.data.models import Liquidation

logger = get_logger("data.liquidations_ws")

BYBIT_PUBLIC_WS_LINEAR = "wss://stream.bybit.com/v5/public/linear"
_MAX_SYMBOLS = 60  # лимит подписок на одно WS-соединение
_PING_SECONDS = 20.0
_PURGE_SECONDS = 900.0  # события старше 15 минут вычищаются из буфера


def parse_liquidation_message(payload: dict[str, Any]) -> list[Liquidation]:
    """Разобрать одно WS-сообщение топика ``liquidation.*`` в события.

    Возвращает пустой список для служебных сообщений (pong/subscribe ответы)
    и для не-ливидационных топиков. ``data`` может быть словарём или списком.
    """
    topic = str(payload.get("topic") or "")
    if not topic.startswith("liquidation."):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = [d for d in data if isinstance(d, dict)]
    else:
        return []
    out: list[Liquidation] = []
    for row in rows:
        try:
            price = float(row.get("price") or 0.0)
            qty = float(row.get("size") or 0.0)
            ts_ms = int(row.get("updatedTime") or payload.get("ts") or 0)
            symbol = str(row.get("symbol") or topic.split(".", 1)[1]).upper()
            side = str(row.get("side") or "")
        except (TypeError, ValueError):
            continue
        if price <= 0 or qty <= 0 or ts_ms <= 0 or not symbol or side not in ("Buy", "Sell"):
            continue
        out.append(
            Liquidation(
                symbol=symbol,
                side=side,  # сторона ЛИКВИДИРОВАННОЙ позиции (Buy = ликвидирован шорт)
                size=price * qty,
                qty=qty,
                price=price,
                ts_ms=ts_ms,
            )
        )
    return out


class LiquidationBuffer:
    """Кольцевой буфер последних ликвидаций (быстрый фильтр по символу/возрасту)."""

    def __init__(self, maxlen: int = 2000) -> None:
        self._events: deque[Liquidation] = deque(maxlen=maxlen)

    def add(self, events: list[Liquidation]) -> None:
        self._events.extend(events)

    def purge_older_than(self, max_age_seconds: float, now_ms: int | None = None) -> int:
        """Удалить события старше ``max_age_seconds``. Возвращает число удалённых.

        Буфер заполняется в порядке ПРИХОДА сообщений, а события бирж может
        быть не упорядочены по биржевому ts — поэтому purge фильтрует весь
        буфер, а не только голову.
        """
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        cutoff = now_ms - int(max_age_seconds * 1000)
        before = len(self._events)
        if before:
            kept = deque((e for e in self._events if e.ts_ms >= cutoff), maxlen=self._events.maxlen)
            self._events = kept
        return before - len(self._events)

    def events(
        self,
        symbol: str | None = None,
        max_age_seconds: float = _PURGE_SECONDS,
        now_ms: int | None = None,
    ) -> list[Liquidation]:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        cutoff = now_ms - int(max_age_seconds * 1000)
        out = [
            e for e in self._events
            if e.ts_ms >= cutoff and (symbol is None or e.symbol.upper() == symbol.upper())
        ]
        out.sort(key=lambda e: e.ts_ms, reverse=True)
        return out

    def __len__(self) -> int:
        return len(self._events)


class BybitLiquidationStream:
    """Один постоянный WS-коллектор публичных ликвидаций Bybit на процесс."""

    def __init__(
        self,
        url: str = BYBIT_PUBLIC_WS_LINEAR,
        max_age_seconds: float = _PURGE_SECONDS,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.url = url
        self.max_age_seconds = max_age_seconds
        self.buffer = LiquidationBuffer()
        self.symbols: set[str] = set()
        self.enabled = True
        # диагностика для pulse / /status
        self.connected = False
        self.started = False
        self.reconnects = 0
        self.last_error = ""
        self.consecutive_errors = 0
        self.last_event_ms = 0
        self.last_connect_ms = 0
        # точки инъекции для юнит-тестов (без реальной сети)
        self._session_factory = session_factory
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._ws: Any = None

    # ── управление ──────────────────────────────────────────────
    async def start(self, symbols: list[str] | None = None) -> None:
        if symbols:
            self.update_symbols(symbols)
        if self._task is None or self._task.done():
            self._stop.clear()
            self.started = True
            self._task = asyncio.create_task(self._run(), name="bybit-liquidations-ws")

    def update_symbols(self, symbols: list[str]) -> None:
        wanted = sorted({s.upper() for s in symbols if s})
        self.symbols = set(wanted[:_MAX_SYMBOLS])

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.connected = False
        self.started = False

    # ── данные ──────────────────────────────────────────────────
    def events(self, symbol: str | None = None, max_age_seconds: float | None = None) -> list[Liquidation]:
        return self.buffer.events(symbol, max_age_seconds or self.max_age_seconds)

    @property
    def healthy(self) -> bool:
        """Поток жив: подключён и сообщения приходили недавно (или ждём первое)."""
        if not self.enabled or not self.started:
            return False
        if not self.connected:
            return False
        silent_ms = int(time.time() * 1000) - (self.last_event_ms or self.last_connect_ms)
        return silent_ms < 120_000

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "started": self.started,
            "connected": self.connected,
            "healthy": self.healthy,
            "reconnects": self.reconnects,
            "consecutive_errors": self.consecutive_errors,
            "last_error": self.last_error,
            "last_event_ms": self.last_event_ms,
            "buffered_events": len(self.buffer),
            "symbols": len(self.symbols),
        }

    # ── внутреннее ──────────────────────────────────────────────
    def _new_session(self) -> Any:
        import aiohttp

        if self._session_factory is not None:
            return self._session_factory()
        return aiohttp.ClientSession()

    async def _subscribe(self, ws: Any, symbols: list[str]) -> None:
        for i in range(0, len(symbols), 10):
            chunk = symbols[i : i + 10]
            await ws.send_json({"op": "subscribe", "args": [f"liquidation.{s}" for s in chunk]})

    async def _ping_loop(self, ws: Any) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.sleep(_PING_SECONDS)
                await ws.send_json({"op": "ping"})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                return

    async def _connect_once(self) -> None:
        """Одно подключение: подписка, приём, ping. Бросает ошибку наружу."""
        import aiohttp

        session = self._new_session()
        async with session:
            async with session.ws_connect(self.url, heartbeat=25.0) as ws:
                self._ws = ws
                self.connected = True
                self.last_connect_ms = int(time.time() * 1000)
                self.consecutive_errors = 0
                self.last_error = ""
                if self.symbols:
                    await self._subscribe(ws, sorted(self.symbols))
                logger.info("Bybit WS ликвидаций: подписка на %d символов", len(self.symbols))
                ping = asyncio.create_task(self._ping_loop(ws))
                try:
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        if raw.type != aiohttp.WSMsgType.TEXT:
                            if raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        try:
                            payload = raw.json()
                        except ValueError:
                            continue
                        events = parse_liquidation_message(payload if isinstance(payload, dict) else {})
                        if events:
                            self.buffer.add(events)
                            self.buffer.purge_older_than(self.max_age_seconds)
                            self.last_event_ms = max(e.ts_ms for e in events)
                finally:
                    ping.cancel()
                self._ws = None
                self.connected = False

    async def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                err: Exception | None = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                err = exc
            self.connected = False
            self._ws = None
            if self._stop.is_set():
                break
            self.reconnects += 1
            self.consecutive_errors += 1
            self.last_error = (str(err)[:200] if err else "соединение закрыто") or "соединение закрыто"
            logger.warning(
                "Bybit WS ликвидаций: переподключение через %.0fс (%s)",
                delay, self.last_error,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, 60.0)
