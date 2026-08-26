"""
Локальное хранилище (SQLite): сигналы, позиции, найденные монеты, новости.
Синхронный stdlib sqlite3 — операции короткие, в WAL-режиме.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from src.core.logging import get_logger

logger = get_logger("core.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    score REAL NOT NULL,
    tier TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts_ms);

CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    entry REAL NOT NULL,
    stop_loss REAL NOT NULL,
    targets TEXT NOT NULL,
    opened_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    closed_at INTEGER,
    close_price REAL,
    pnl_pct REAL,
    note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS gems (
    ts_ms INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    score REAL NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (ts_ms, symbol)
);
CREATE INDEX IF NOT EXISTS idx_gems_ts ON gems(ts_ms);

CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY,
    ts_ms INTEGER NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT DEFAULT '',
    symbols TEXT DEFAULT '[]',
    sentiment REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_news_ts ON news(ts_ms);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    """Простое потокобезопасное хранилище на SQLite."""

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

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ── СИГНАЛЫ ────────────────────────────────────────────────
    def save_signal(self, ts_ms: int, symbol: str, direction: str, score: float, tier: str, payload: dict) -> None:
        self._execute(
            "INSERT INTO signals (ts_ms, symbol, direction, score, tier, payload) VALUES (?,?,?,?,?,?)",
            (ts_ms, symbol, direction, score, tier, json.dumps(payload, ensure_ascii=False, default=str)),
        )

    def recent_signals(self, limit: int = 100, symbol: str | None = None) -> list[dict]:
        if symbol:
            rows = self._query(
                "SELECT * FROM signals WHERE symbol=? ORDER BY ts_ms DESC LIMIT ?", (symbol, limit)
            )
        else:
            rows = self._query("SELECT * FROM signals ORDER BY ts_ms DESC LIMIT ?", (limit,))
        return [self._signal_to_dict(r) for r in rows]

    @staticmethod
    def _signal_to_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
        return d

    # ── ПОЗИЦИИ (бумажное отслеживание советов) ────────────────
    def upsert_position(
        self,
        symbol: str,
        side: str,
        entry: float,
        stop_loss: float,
        targets: list[float],
        note: str = "",
    ) -> None:
        self._execute(
            """
            INSERT INTO positions (symbol, side, entry, stop_loss, targets, opened_at, status, note)
            VALUES (?,?,?,?,?,?, 'open', ?)
            ON CONFLICT(symbol) DO UPDATE SET
                side=excluded.side, entry=excluded.entry, stop_loss=excluded.stop_loss,
                targets=excluded.targets, opened_at=excluded.opened_at, status='open', note=excluded.note
            """,
            (
                symbol,
                side,
                entry,
                stop_loss,
                json.dumps(targets),
                __import__("src.core.timeutil", fromlist=["now_ms"]).now_ms(),
                note,
            ),
        )

    def positions(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._query("SELECT * FROM positions WHERE status=? ORDER BY opened_at DESC", (status,))
        else:
            rows = self._query("SELECT * FROM positions ORDER BY opened_at DESC")
        out = []
        for r in rows:
            d = dict(r)
            d["targets"] = json.loads(d["targets"])
            out.append(d)
        return out

    def close_position(self, symbol: str, price: float, note: str = "") -> None:
        pos = self.positions("open")
        for p in pos:
            if p["symbol"] == symbol:
                side = 1 if p["side"].upper() == "LONG" else -1
                pnl = side * (price - p["entry"]) / p["entry"] * 100
                self._execute(
                    "UPDATE positions SET status='closed', close_price=?, pnl_pct=?, closed_at=?, note=? WHERE symbol=?",
                    (price, round(pnl, 3), __import__("src.core.timeutil", fromlist=["now_ms"]).now_ms(), note, symbol),
                )
                return

    def update_position_status(self, symbol: str, status: str, note: str = "") -> None:
        self._execute("UPDATE positions SET status=?, note=? WHERE symbol=?", (status, symbol, note))

    # ── НАЙДЕННЫЕ МОНЕТЫ ───────────────────────────────────────
    def save_gems(self, ts_ms: int, gems: list[dict]) -> None:
        with self._lock:
            for g in gems:
                self._conn.execute(
                    "INSERT OR REPLACE INTO gems (ts_ms, symbol, source, score, payload) VALUES (?,?,?,?,?)",
                    (
                        ts_ms,
                        g["symbol"],
                        g.get("source", ""),
                        float(g.get("score", 0.0)),
                        json.dumps(g, ensure_ascii=False, default=str),
                    ),
                )
            self._conn.commit()

    def latest_gems(self, limit: int = 100) -> list[dict]:
        rows = self._query(
            "SELECT * FROM gems WHERE ts_ms = (SELECT MAX(ts_ms) FROM gems) ORDER BY score DESC LIMIT ?",
            (limit,),
        )
        out = []
        for r in rows:
            d = json.loads(r["payload"])
            d.setdefault("symbol", r["symbol"])
            d.setdefault("source", r["source"])
            out.append(d)
        return out

    # ── НОВОСТИ ────────────────────────────────────────────────
    def save_news(self, items: list[dict]) -> None:
        with self._lock:
            for n in items:
                self._conn.execute(
                    "INSERT OR REPLACE INTO news (id, ts_ms, source, title, url, symbols, sentiment) VALUES (?,?,?,?,?,?,?)",
                    (
                        n["id"],
                        n.get("ts_ms", 0),
                        n.get("source", ""),
                        n.get("title", ""),
                        n.get("url", ""),
                        json.dumps(n.get("symbols", [])),
                        float(n.get("sentiment", 0.0)),
                    ),
                )
            self._conn.execute("DELETE FROM news WHERE ts_ms < ?", (n.get("ts_ms", 0) - 3 * 86_400_000,))
            self._conn.commit()

    def recent_news(self, limit: int = 40) -> list[dict]:
        rows = self._query("SELECT * FROM news ORDER BY ts_ms DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            d["symbols"] = json.loads(d["symbols"])
            out.append(d)
        return out

    # ── СОСТОЯНИЕ ──────────────────────────────────────────────
    def set_state(self, key: str, value: str) -> None:
        self._execute("INSERT OR REPLACE INTO state (key, value) VALUES (?,?)", (key, value))

    def get_state(self, key: str, default: str = "") -> str:
        rows = self._query("SELECT value FROM state WHERE key=?", (key,))
        return rows[0]["value"] if rows else default
