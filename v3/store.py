"""SQLite persistence for v3 signals and outcomes.

The schema is deliberately small and stable (signals + outcomes + system
state).  Market data is not stored by default -- features and scores are,
which is enough to audit/retrain the model and reproduce a signal.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from v3.models import TradingSignal

SCHEMA = """
CREATE TABLE IF NOT EXISTS v3_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL NOT NULL,
    confidence REAL NOT NULL,
    quality REAL NOT NULL,
    tier TEXT NOT NULL,
    regime TEXT NOT NULL,
    price REAL NOT NULL,
    entry REAL NOT NULL,
    sl REAL NOT NULL,
    rr REAL NOT NULL,
    payload TEXT NOT NULL,
    index_ts INTEGER NOT NULL,
    created_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v3_signals_symbol ON v3_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_v3_signals_ts ON v3_signals(ts_ms);

CREATE TABLE IF NOT EXISTS v3_outcomes (
    signal_uid TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    opened_at INTEGER NOT NULL,
    exit_at INTEGER,
    exit_reason TEXT,
    mfe_pct REAL,
    mae_pct REAL,
    duration_min REAL,
    outcome TEXT,
    r_multiple REAL,
    pnl_pct REAL
);
CREATE INDEX IF NOT EXISTS idx_v3_outcomes_symbol ON v3_outcomes(symbol);

CREATE TABLE IF NOT EXISTS v3_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SignalStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def save_signal(self, signal: TradingSignal) -> None:
        payload = json.dumps(signal.to_dict(), ensure_ascii=False, default=str)
        self._execute(
            """
            INSERT OR REPLACE INTO v3_signals
            (uid, symbol, ts_ms, direction, status, score, confidence, quality, tier,
             regime, price, entry, sl, rr, payload, index_ts, created_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signal.uid,
                signal.symbol,
                signal.ts_ms,
                signal.direction,
                signal.status,
                signal.score,
                signal.confidence,
                signal.quality,
                signal.tier,
                signal.regime,
                signal.price,
                signal.entry_price,
                signal.stop_loss,
                signal.rr,
                payload,
                signal.ts_ms,
                signal.created_ms,
            ),
        )

    def get_signal(self, uid: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM v3_signals WHERE uid=?", (uid,))
        return self._row_to_dict(rows[0]) if rows else None

    def recent_signals(self, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if symbol:
            rows = self._query(
                "SELECT * FROM v3_signals WHERE symbol=? ORDER BY ts_ms DESC LIMIT ?", (symbol.upper(), limit)
            )
        else:
            rows = self._query("SELECT * FROM v3_signals ORDER BY ts_ms DESC LIMIT ?", (limit,))
        return [self._row_to_dict(r) for r in rows]

    def latest_sent_at(self, symbol: str) -> int:
        rows = self._query(
            "SELECT MAX(ts_ms) AS ts FROM v3_signals WHERE symbol=? AND direction IN ('LONG','SHORT')",
            (symbol.upper(),),
        )
        return int(rows[0]["ts"] or 0) if rows else 0

    def save_outcome(
        self,
        uid: str,
        symbol: str,
        opened_at: int,
        exit_at: int | None = None,
        exit_reason: str = "",
        mfe_pct: float = 0.0,
        mae_pct: float = 0.0,
        outcome: str = "OPEN",
        r_multiple: float | None = None,
        pnl_pct: float | None = None,
    ) -> None:
        duration = (exit_at - opened_at) / 60000 if exit_at and opened_at else None
        self._execute(
            """
            INSERT OR REPLACE INTO v3_outcomes
            (signal_uid, symbol, opened_at, exit_at, exit_reason, mfe_pct, mae_pct,
             duration_min, outcome, r_multiple, pnl_pct)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                uid,
                symbol.upper(),
                int(opened_at),
                int(exit_at) if exit_at else None,
                exit_reason,
                mfe_pct,
                mae_pct,
                round(duration, 2) if duration else None,
                outcome,
                r_multiple,
                pnl_pct,
            ),
        )

    def outcomes(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            rows = self._query("SELECT * FROM v3_outcomes WHERE symbol=? ORDER BY opened_at DESC", (symbol.upper(),))
        else:
            rows = self._query("SELECT * FROM v3_outcomes ORDER BY opened_at DESC")
        return [dict(r) for r in rows]

    def set_state(self, key: str, value: str) -> None:
        self._execute("INSERT OR REPLACE INTO v3_state (key, value) VALUES (?,?)", (key, value))

    def get_state(self, key: str, default: str = "") -> str:
        rows = self._query("SELECT value FROM v3_state WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    @staticmethod
    def _row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload", "{}")) or {}
        return d

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class SignalLifecycle:
    """Cooldown + deduplication + active-signal bookkeeping."""

    def __init__(self, store: SignalStore, cooldown_seconds: int = 3600, max_active: int = 12) -> None:
        self.store = store
        self.cooldown_seconds = cooldown_seconds
        self.max_active = max_active
        self._active: dict[str, TradingSignal] = {}
        self._last_emitted: dict[str, int] = {}

    def should_emit(self, signal: TradingSignal, now_ms: int | None = None) -> tuple[bool, str]:
        now_ms = now_ms or int(time.time() * 1000)
        if signal.direction not in ("LONG", "SHORT"):
            return False, "not a trade"
        last = self._last_emitted.get(signal.symbol, self.store.latest_sent_at(signal.symbol))
        if last and now_ms - last < self.cooldown_seconds * 1000:
            return False, "cooldown"
        if len(self._active) >= self.max_active and signal.symbol not in self._active:
            return False, "max_active_reached"
        return True, ""

    def register(self, signal: TradingSignal) -> None:
        self._active[signal.symbol] = signal
        self._last_emitted[signal.symbol] = int(time.time() * 1000)
        self.store.save_signal(signal)

    def active(self) -> list[TradingSignal]:
        return list(self._active.values())

    def update_status(self, signal: TradingSignal, status: str) -> None:
        signal.status = status
        signal.updated_ms = int(time.time() * 1000)
        if status in ("CLOSED", "STOPPED", "INVALIDATED", "EXPIRED"):
            self._active.pop(signal.symbol, None)
        self.store.save_signal(signal)

    # ── price tracking (TP / SL / MFE / MAE) ───────────────────
    def track_prices(self, prices: dict[str, float], now_ms: int | None = None) -> list[dict[str, Any]]:
        """Track active signals against the latest price.

        Returns a list of closed events.  A position is considered closed when
        either its stop or its final target is hit.  TP1/TP2 are recorded as
        status transitions; they do not close the whole position.
        """
        now_ms = now_ms or int(time.time() * 1000)
        events: list[dict[str, Any]] = []
        for symbol, signal in list(self._active.items()):
            price = prices.get(symbol)
            if price is None or price <= 0:
                continue
            if signal.direction not in ("LONG", "SHORT"):
                continue

            entry = signal.entry_price
            if entry <= 0:
                entry = signal.price
            if entry <= 0:
                continue

            if signal.status == "GENERATED":
                signal.status = "ACTIVE"
            signal.updated_ms = now_ms

            is_long = signal.direction == "LONG"
            change = (price - entry) / entry * 100.0
            mfe = change if is_long else -change
            mae = -change if is_long else change

            # stop loss / invalidation
            stopped = (price <= signal.stop_loss) if is_long else (price >= signal.stop_loss)
            tp3 = signal.targets[2] if len(signal.targets) >= 3 else (signal.targets[-1] if signal.targets else 0)
            tp2 = signal.targets[1] if len(signal.targets) >= 2 else tp3
            tp1 = signal.targets[0] if signal.targets else tp3
            hit1 = (price >= tp1) if is_long else (price <= tp1)
            hit2 = (price >= tp2) if is_long else (price <= tp2)
            hit3 = (price >= tp3) if is_long else (price <= tp3)

            if stopped:
                signal.status = "STOPPED"
                self.store.save_outcome(
                    signal.uid, signal.symbol, signal.ts_ms, now_ms,
                    exit_reason="stop_loss", mfe_pct=mfe, mae_pct=mae,
                    outcome="LOSS", r_multiple=-1.0,
                    pnl_pct=(-abs(change) if is_long else -abs(change)),
                )
                self.store.save_signal(signal)
                events.append({"symbol": symbol, "event": "STOPPED", "price": price})
                self._active.pop(symbol, None)
            elif hit3:
                pnl = change if is_long else -change
                signal.status = "CLOSED"
                self.store.save_outcome(
                    signal.uid, signal.symbol, signal.ts_ms, now_ms,
                    exit_reason="tp3", mfe_pct=mfe, mae_pct=mae,
                    outcome="WIN", r_multiple=3.0, pnl_pct=pnl,
                )
                self.store.save_signal(signal)
                events.append({"symbol": symbol, "event": "TP3_HIT", "price": price})
                self._active.pop(symbol, None)
            elif hit2 and signal.status not in ("TP2_HIT", "TP3_HIT"):
                signal.status = "TP2_HIT"
                events.append({"symbol": symbol, "event": "TP2_HIT", "price": price})
            elif hit1 and signal.status not in ("TP1_HIT", "TP2_HIT", "TP3_HIT"):
                signal.status = "TP1_HIT"
                events.append({"symbol": symbol, "event": "TP1_HIT", "price": price})

            if symbol in self._active:
                self.store.save_signal(signal)
        return events
