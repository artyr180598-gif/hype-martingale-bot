"""Background watcher / scanner loop for v3.

It analyses the liquid universe (or an explicit watchlist) every
``WATCHER_INTERVAL_SECONDS``, persists EVERY observation, and pushes to the
chat only the setups that pass the auto-signal gate
(``v3.alerts.evaluate_alert``).  Active signals are tracked against the latest
price so TP/SL results are written to SQLite and reported as events.

Два независимых фильтра — и они не дублируют друг друга:
  * ``SignalLifecycle.should_emit`` — «можно ли публиковать» (cooldown, лимит
    активных);
  * ``evaluate_alert`` — «стоит ли будить пользователя» (качество, уверенность
    бота, полнота данных, риск, потенциал к риску, свежесть).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from v3.alerts import AlertItem, evaluate_alert, stopout_pause
from v3.config import SignalConfig
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.store import SignalLifecycle, SignalStore

AlertSender = Callable[[list[AlertItem]], Awaitable[None]]


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
        # daemon без явного списка должен искать ранние импульсы по всей
        # доступной вселенной. Команда `watch BTCUSDT,ETHUSDT` остаётся
        # точечным режимом и не делает сотни запросов.
        self.universe_scan = bool(self.cfg.WATCHER_SCAN_UNIVERSE and symbols is None)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_cycle_ms = 0
        self.interval_seconds = int(self.cfg.WATCHER_INTERVAL_SECONDS)
        # Что было отправлено/отклонено в последнем цикле — видно в разделе
        # «🔔 АВТО-СИГНАЛЫ»: бот не должен молчать без объяснения причины.
        self.last_alerts: list[AlertItem] = []
        self.last_checked = 0
        self.last_suppressed: str = ""

    # ── авто-сигналы: вкл/пауза (переключается из Telegram) ─────
    @property
    def alerts_enabled(self) -> bool:
        raw = self.store.get_state("v3_alerts_enabled", "")
        if raw in ("1", "true", "True"):
            return True
        if raw in ("0", "false", "False"):
            return False
        return bool(self.cfg.ALERTS_ENABLED)

    def set_alerts_enabled(self, enabled: bool) -> bool:
        self.store.set_state("v3_alerts_enabled", "1" if enabled else "0")
        return enabled

    def toggle_alerts(self) -> bool:
        return self.set_alerts_enabled(not self.alerts_enabled)

    @property
    def alerts_found_total(self) -> int:
        try:
            return int(self.store.get_state("v3_alerts_found", "0") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def alerts_sent_total(self) -> int:
        try:
            return int(self.store.get_state("v3_alerts_sent", "0") or 0)
        except (TypeError, ValueError):
            return 0

    async def run_cycle(self, notify: AlertSender | None = None) -> list[dict[str, Any]]:
        from v3.publisher import sanitize_for_publish

        # Полный daemon-контур использует тот же двухэтапный Scanner, что и
        # Telegram/API: сначала все ликвидные USDT-perp, затем глубокий анализ
        # ранних кандидатов. Это исправляет главный практический недостаток
        # «watchlist-only» режима — новая монета не должна ждать ручного scan.
        signals = []
        ticker_map: dict[str, Any] = {}
        scanned_universe = False
        if self.universe_scan:
            try:
                from v3.scanner import Scanner

                ticker_map = await self.data.tickers()
                if ticker_map:
                    scanner = Scanner(self.engine, self.cfg)
                    result = await scanner.run(
                        ticker_map,
                        limit=self.cfg.SCAN_LIMIT,
                        top=self.cfg.SCAN_TOP,
                    )
                    signals = [item["signal"] for item in result.analyzed]
                    scanned_universe = True
                    self.store.set_state("last_scan_ms", str(result.ts_ms))
                    self.store.set_state("v3_last_scan_ms", str(result.ts_ms))
            except Exception as exc:  # noqa: BLE001
                # Ошибка полного скана не должна остановить lifecycle. Ниже
                # остаётся безопасный fallback на явный watchlist.
                self.store.set_state("v3_last_error", f"universe scan: {exc}")

        if not scanned_universe:
            signals = await self.engine.analyze_batch(self.watchlist, concurrency=4)

        # ``emitted`` is kept as the list of observations processed by this
        # cycle (the return contract used by the CLI/tests). It is deliberately
        # different from ``alert_items``: only the latter can reach Telegram.
        emitted: list[dict[str, Any]] = []
        worthy: list[tuple[Any, Any]] = []
        suppressed: list[str] = []
        for raw in signals:
            sig, violations = sanitize_for_publish(raw, self.cfg)
            if violations:
                sig.no_trade_reasons = list(dict.fromkeys(sig.no_trade_reasons + violations))[:8]

            # Evaluate before saving the observation. ``latest_sent_at`` is the
            # persistent cooldown source and must not see the current snapshot
            # as if it were an earlier alert.
            decision = evaluate_alert(sig, self.cfg)
            allowed = False
            lifecycle_reason = ""
            if decision.ok:
                # Пауза после серии стопов по этой монете: сетап остаётся в
                # базе и в разделах списков, но в чат не летит.
                paused, why = stopout_pause(self.store.outcomes(sig.symbol), self.cfg)
                if paused:
                    suppressed.append(f"{sig.symbol}: {why}")
                else:
                    # Do this before persistence; a candidate that is not sent
                    # must not consume the cooldown of a future good setup.
                    allowed, lifecycle_reason = self.lifecycle.should_emit(sig)
                    if not allowed:
                        suppressed.append(f"{sig.symbol}: {lifecycle_reason}")
                    else:
                        worthy.append((sig, decision))
            else:
                suppressed.append(
                    f"{sig.symbol}: {decision.reasons[0] if decision.reasons else 'порог не пройден'}"
                )

            # Persist every observation for the audit/history screens, including
            # weak and NO_TRADE setups. Only selected worthy setups are
            # registered below, so an unsent setup cannot later generate a
            # TP/SL message without an entry notification.
            self.store.save_signal(sig)
            emitted.append(sig.to_dict())

        # Select the strongest candidates before registering them. This avoids
        # tracking an extra setup as active when the per-cycle notification cap
        # means it will not actually be sent.
        worthy.sort(key=lambda item: item[1].percent, reverse=True)
        alert_items: list[AlertItem] = []
        selected_symbols: set[str] = set()
        max_alerts = max(1, self.cfg.ALERT_MAX_PER_CYCLE)
        for sig, decision in worthy:
            if sig.symbol in selected_symbols:
                suppressed.append(f"{sig.symbol}: повторный кандидат в одном цикле")
                continue
            if len(alert_items) >= max_alerts:
                suppressed.append(f"{sig.symbol}: лимит {max_alerts} уведомлений за цикл")
                continue
            # ``should_emit`` was checked before the observation was saved. The
            # explicit active check here accounts for several new symbols
            # competing for the remaining slots in this same cycle.
            if len(self.lifecycle.active()) >= self.lifecycle.max_active and sig.symbol not in self.lifecycle._active:
                suppressed.append(f"{sig.symbol}: max_active_reached")
                continue
            self.lifecycle.register(sig)
            selected_symbols.add(sig.symbol)
            alert_items.append(AlertItem(kind="signal", signal=sig, decision=decision))

        self.last_checked = len(emitted)
        self.last_suppressed = suppressed[0] if suppressed else ""

        # track active signals using latest ticker prices
        try:
            if not ticker_map:
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
        event_items = [AlertItem(kind="event", event=e) for e in events]

        self.last_cycle_ms = int(time.time() * 1000)
        self.last_alerts = alert_items + event_items
        self.store.set_state("v3_last_cycle_ms", str(self.last_cycle_ms))
        self.store.set_state("v3_signal_count", str(len(self.store.recent_signals(limit=10_000))))
        self.store.set_state("v3_last_checked", str(self.last_checked))
        if self.last_suppressed:
            self.store.set_state("v3_last_suppressed", self.last_suppressed)
        # «Найдено достойных» и «отправлено» — разные счётчики: бот может
        # найти сетап, но не доставить его (пауза, нет транспорта, ошибка).
        # Считать недоставленное как отправленное — значит врать в UI.
        if alert_items:
            self.store.set_state("v3_alerts_found", str(self.alerts_found_total + len(alert_items)))
            self.store.set_state("v3_last_found_symbol", ", ".join(i.symbol for i in alert_items[:2]))

        if notify and (alert_items or event_items):
            if not self.alerts_enabled:
                self.store.set_state("v3_alerts_paused_at", str(self.last_cycle_ms))
            else:
                try:
                    await notify(alert_items + event_items)
                except Exception as exc:  # noqa: BLE001
                    self.store.set_state("v3_alert_error", f"{type(exc).__name__}: {exc}")
                else:
                    if alert_items:
                        self.store.set_state("v3_alerts_sent", str(self.alerts_sent_total + len(alert_items)))
                        self.store.set_state("v3_last_alert_ms", str(self.last_cycle_ms))
                        self.store.set_state(
                            "v3_last_alert_symbol", ", ".join(i.symbol for i in alert_items[:2])
                        )
        return emitted + events

    async def start(self, notify: AlertSender | None = None, interval: int | None = None) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.interval_seconds = int(interval or self.cfg.WATCHER_INTERVAL_SECONDS)
        self._task = asyncio.create_task(
            self._loop(notify, interval or self.cfg.WATCHER_INTERVAL_SECONDS), name="v3.watcher"
        )

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
