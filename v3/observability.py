"""Lightweight observability for the v3 engine.

Structured logging already comes from ``src.core.logging``.  This module adds
process-level metrics, last-error memory and a health snapshot so an operator
can quickly answer "why is the bot not producing signals?".
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.logging import get_logger

logger = get_logger("v3.observability")


@dataclass
class HealthSnapshot:
    started_at: float = field(default_factory=time.time)
    mode: str = "unknown"
    data_ok: bool = False
    ws_ok: bool | None = None
    scanner_ok: bool | None = None
    db_ok: bool | None = None
    last_analysis_ms: int = 0
    last_error: str = ""
    analyses: int = 0
    scan_results: int = 0
    signals_saved: int = 0
    active_signals: int = 0
    outcomes: int = 0
    auth_denials: int = 0
    latency_avg_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeMetrics:
    def __init__(self) -> None:
        self._started = time.time()
        self._lock = threading.Lock()
        self._analyses = 0
        self._scan_results = 0
        self._errors: list[str] = []
        self._last_analysis_ms = 0
        self._latencies: list[float] = []
        self._last_error = ""
        self._mode = "unknown"
        self._data_ok = False
        self._auth_denials = 0

    def mark_mode(self, mode: str, data_ok: bool) -> None:
        with self._lock:
            self._mode = mode
            self._data_ok = data_ok

    def record_auth_denial(self) -> None:
        with self._lock:
            self._auth_denials += 1

    def record_analysis(self, symbol: str, duration_sec: float) -> None:
        with self._lock:
            self._analyses += 1
            self._last_analysis_ms = int(time.time() * 1000)
            self._latencies.append(duration_sec * 1000.0)
            if len(self._latencies) > 200:
                self._latencies = self._latencies[-200:]

    def record_scan(self) -> None:
        with self._lock:
            self._scan_results += 1

    def record_error(self, component: str, exc: Any) -> None:
        msg = f"{component}: {exc}"
        with self._lock:
            self._last_error = msg[:500]
            self._errors.append(msg[:500])
            if len(self._errors) > 100:
                self._errors = self._errors[-100:]
        logger.warning("v3 error: %s", msg)

    def snapshot(
        self,
        *,
        db_ok: bool | None = None,
        scanner_ok: bool | None = None,
        active_signals: int = 0,
        signals_saved: int = 0,
        outcomes: int = 0,
    ) -> HealthSnapshot:
        with self._lock:
            avg_lat = sum(self._latencies) / len(self._latencies) if self._latencies else 0.0
            return HealthSnapshot(
                started_at=self._started,
                mode=self._mode,
                data_ok=self._data_ok,
                db_ok=db_ok,
                scanner_ok=scanner_ok,
                last_analysis_ms=self._last_analysis_ms,
                last_error=self._last_error,
                analyses=self._analyses,
                scan_results=self._scan_results,
                signals_saved=signals_saved,
                active_signals=active_signals,
                outcomes=outcomes,
                auth_denials=self._auth_denials,
                latency_avg_ms=round(avg_lat, 1),
            )

    def recent_errors(self, limit: int = 10) -> list[str]:
        with self._lock:
            return self._errors[-limit:]


metrics = RuntimeMetrics()
