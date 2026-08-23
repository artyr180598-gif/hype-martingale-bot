"""
Real-Time WebSocket Stream Manager with Auto-Reconnect.
"""
import asyncio
import json
from collections.abc import Callable

import aiohttp

from src.core.logging import get_logger

logger = get_logger("data.streams")


class WebSocketStreamManager:
    """
    Manages resilient real-time WebSocket subscriptions for Binance/Bybit Futures streams.
    """

    def __init__(self, base_ws_url: str = "wss://fstream.binance.com/ws"):
        self.base_ws_url = base_ws_url
        self._subscriptions: set[str] = set()
        self._callbacks: dict[str, list[Callable]] = {}
        self._running = False
        self._task: asyncio.Task | None = None

    def subscribe(self, stream_name: str, callback: Callable) -> None:
        self._subscriptions.add(stream_name)
        if stream_name not in self._callbacks:
            self._callbacks[stream_name] = []
        self._callbacks[stream_name].append(callback)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _run_loop(self) -> None:
        while self._running:
            try:
                streams_str = "/".join(self._subscriptions)
                url = f"{self.base_ws_url}/{streams_str}" if streams_str else self.base_ws_url
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, heartbeat=15.0) as ws:
                        logger.info("WebSocket connected to market stream", streams=list(self._subscriptions))
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                stream = data.get("stream", "default")
                                for cb in self._callbacks.get(stream, []):
                                    try:
                                        cb(data.get("data", data))
                                    except Exception as e:
                                        logger.error("Callback error", stream=stream, error=str(e))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("WebSocket disconnected, reconnecting in 5s...", error=str(e))
                await asyncio.sleep(5.0)
