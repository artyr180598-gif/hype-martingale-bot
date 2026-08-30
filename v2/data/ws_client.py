"""
WebSocket-клиент с автопереподключением.

Заменяет опрос REST-эндпоинтов в цикле: тикеры/стаканы приходят пушем, поэтому
сканер уровня 1 видит пятиминутный объём «в реальном времени», а не с лагом
опроса. Клиент:

  * сам переподключается с экспоненциальной задержкой (1 → 2 → 4 … → 60с);
  * следит за «протуханием»: если сообщений нет дольше WS_STALE_SECONDS,
    соединение рвётся и пересоздаётся (тихий разрыв — частая болячка WS);
  * шлёт ping, чтобы провайдер не закрыл соединение по idle-timeout;
  * при переподключении заново отправляет подписки;
  * любые ошибки уходят в ErrorMonitor, процесс не падает.

Если aiohttp недоступен или провайдер не отвечает, вызывающий код должен
переключиться на REST-опрос (это делает v2/data/cex.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any, Awaitable, Callable, Iterable

from v2.config import V2Config
from v2.core.logging import get_logger
from v2.core.monitor import health, monitor

logger = get_logger("data.ws")

OnMessage = Callable[[dict[str, Any]], Awaitable[None]]


class WebSocketStream:
    """Один канал = одно соединение со своим набором подписок."""

    def __init__(
        self,
        config: V2Config,
        url: str,
        *,
        name: str = "ws",
        subscribe_payload: dict[str, Any] | list[dict[str, Any]] | None = None,
        on_message: OnMessage | None = None,
    ) -> None:
        self.config = config
        self.url = url
        self.name = name
        self.subscribe_payload = subscribe_payload
        self.on_message = on_message
        self.messages = 0
        self.reconnects = 0
        self.last_message_ts = 0.0
        self.connected = False
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ── жизненный цикл ───────────────────────────────────────────
    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"ws:{self.name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.connected = False

    # ── основной цикл ────────────────────────────────────────────
    async def _run(self) -> None:
        delay = self.config.WS_RECONNECT_BASE_SECONDS
        while not self._stop.is_set():
            try:
                await self._connect_once()
                delay = self.config.WS_RECONNECT_BASE_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — соединение обязано пережить сбой
                monitor.record(f"ws.{self.name}", exc)
                self.connected = False
            if self._stop.is_set():
                break
            self.reconnects += 1
            health.mark(f"ws.{self.name}.reconnects", self.reconnects)
            logger.warning("%s: переподключение через %.1fс", self.name, delay)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            delay = min(delay * 2, self.config.WS_RECONNECT_MAX_SECONDS)

    async def _connect_once(self) -> None:
        import aiohttp  # импорт здесь: без WS-режима зависимость не обязательна

        logger.info("%s: подключение к %s", self.name, self.url)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=self.config.HTTP_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(
                self.url, heartbeat=self.config.WS_PING_INTERVAL_SECONDS, autoping=True
            ) as ws:
                self.connected = True
                self.last_message_ts = time.time()
                health.mark(f"ws.{self.name}.connected", True)
                if self.subscribe_payload is not None:
                    payloads: Iterable[Any] = (
                        self.subscribe_payload
                        if isinstance(self.subscribe_payload, list)
                        else [self.subscribe_payload]
                    )
                    for payload in payloads:
                        await ws.send_json(payload)
                    logger.debug("%s: отправлено подписок: %s", self.name, self.subscribe_payload)

                stale_after = self.config.WS_STALE_SECONDS
                while not self._stop.is_set():
                    if time.time() - self.last_message_ts > stale_after:
                        logger.warning("%s: нет сообщений %.0fс — рвём соединение", self.name, stale_after)
                        await ws.close()
                        break
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=5.0)
                    except asyncio.TimeoutError:
                        continue
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        data = json.loads(msg.data)
                    except (TypeError, ValueError):
                        continue
                    self.messages += 1
                    self.last_message_ts = time.time()
                    health.mark(f"ws.{self.name}.messages", self.messages)
                    if self.on_message is not None:
                        try:
                            await self.on_message(data)
                        except Exception as exc:  # noqa: BLE001 — колбэк не роняет поток
                            monitor.record(f"ws.{self.name}.callback", exc)
                self.connected = False
                health.mark(f"ws.{self.name}.connected", False)


class TickerAggregator:
    """
    Накапливает тикеры из WS-потока в словарь «символ → скользящие метрики».

    Считает оборот и число сделок за последние N минут по кольцевому буферу
    событий — это ровно те два показателя, по которым фильтрует уровень 1
    сканера (объём за 5 минут > $500k и транзакций > 100).
    """

    def __init__(self, window_sec: float = 300.0) -> None:
        self.window_sec = window_sec
        self._events: dict[str, list[tuple[float, float, int]]] = {}  # symbol -> (ts, quote_volume, trades)
        self._lock = asyncio.Lock()

    async def add_trade(self, symbol: str, quote_volume: float, trades: int = 1) -> None:
        async with self._lock:
            self._events.setdefault(symbol, []).append((time.time(), float(quote_volume), int(trades)))

    async def add_snapshot(self, symbol: str, quote_volume_window: float, trades_window: int) -> None:
        """Для потоков, которые отдают уже агрегированное окно (Bybit tickers)."""
        async with self._lock:
            self._events[symbol] = [(time.time(), float(quote_volume_window), int(trades_window))]

    async def snapshot(self, symbol: str) -> tuple[float, int]:
        """(оборот, число сделок) за окно window_sec."""
        cutoff = time.time() - self.window_sec
        async with self._lock:
            events = [e for e in self._events.get(symbol, []) if e[0] >= cutoff]
            self._events[symbol] = events
            return sum(e[1] for e in events), sum(e[2] for e in events)

    async def all_snapshots(self) -> dict[str, tuple[float, int]]:
        cutoff = time.time() - self.window_sec
        out: dict[str, tuple[float, int]] = {}
        async with self._lock:
            for symbol, events in self._events.items():
                events = [e for e in events if e[0] >= cutoff]
                self._events[symbol] = events
                if events:
                    out[symbol] = (sum(e[1] for e in events), sum(e[2] for e in events))
        return out
