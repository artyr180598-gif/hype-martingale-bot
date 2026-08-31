"""Background watcher / scanner loop for v3.

It analyses watchlist symbols at ``WATCH_INTERVAL_SECONDS``, persists all
signals, emits only signals that pass the lifecycle gate (cooldown / max
active), and tracks active signals against the latest price so TP/SL results
are written to SQLite.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from v3.config import SignalConfig
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.store import SignalLifecycle, SignalStore

AlertSender = Callable[[list[dict[str, Any]]], Awaitable[None]]


class V3Watcher:
    def __init__(
        self,
        data: FuturesDataService,
        engine: FuturesSignalEngine,
        store: SignalStore,
        lifecycle: SignalLifecycle,
        cfg: SignalConfig | None = None,
        symbols: list[str] | None = None,
    ) -> None:
        self.data = data
        self.engine = engine
        self.store = store
        self.lifecycle = lifecycle
        self.cfg = cfg or SignalConfig()
        self.watchlist: list[str] = [s.upper() for s in (symbols or self.cfg.watchlist)]
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_cycle_ms = 0

    async def run_cycle(self, notify: AlertSender | None = None) -> list[dict[str, Any]]:
        signals = await self.engine.analyze_batch(self.watchlist, concurrency=4)
        emitted: list[dict[str, Any]] = []
        for sig in signals:
            self.store.save_signal(sig)
            allowed, reason = self.lifecycle.should_emit(sig)
            if allowed:
                self.lifecycle.register(sig)
                emitted.append(sig.to_dict())
            else:
                sig.no_trade_reasons = [reason] if not sig.no_trade_reasons else sig.no_trade_reasons
                self.store.save_signal(sig)

        # track active signals using latest ticker prices
        try:
            tickers = await self.data.tickers(list(self.lifecycle._active.keys()))
            if isinstance(tickers, list):
                ticker_map = {t.symbol: t for t in tickers}
            else:
                ticker_map = tickers or {}
            prices = {
                symbol: float(t.last)
                for symbol, t in ticker_map.items()
                if float(t.last or 0) > 0
            }
        except Exception:  # noqa: BLE001
            self.store.set_state("v3_track_error", "ticker fetch failed")
            prices = {}
        events = self.lifecycle.track_prices(prices)

        self.last_cycle_ms = int(time.time() * 1000)
        self.store.set_state("v3_last_cycle_ms", str(self.last_cycle_ms))
        self.store.set_state("v3_signal_count", str(len(self.store.recent_signals(limit=10_000))))

        if notify and (emitted or events):
            try:
                await notify(emitted + events)
            except Exception as exc:  # noqa: BLE001
                pass
        return emitted + events

    async def start(self, notify: AlertSender | None = None, interval: int | None = None) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(notify, interval or self.cfg.SCAN_INTERVAL_SECONDS), name="v3.watcher")

    async def _loop(self, notify: AlertSender | None, interval: int) -> None:
        while not self._stop.is_set():
            try:
                await self.run_cycle(notify)
            except Exception as exc:  # noqa: BLE001
                self.store.set_state("v3_last_error", str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(5, interval))
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
