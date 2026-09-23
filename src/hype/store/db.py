"""SQLite storage for signals — ported from v3 store.py but simplified."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from ..config import Settings
from ..signals.models import Signal


class SignalStore:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.db_path = cfg.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    exchange TEXT,
                    direction TEXT,
                    status TEXT,
                    entry REAL,
                    stop_loss REAL,
                    take_profits TEXT,
                    risk_reward REAL,
                    quality_score REAL,
                    quality_grade TEXT,
                    confidence_pct REAL,
                    expected_move_pct REAL,
                    regime TEXT,
                    timeframe TEXT,
                    reasons TEXT,
                    raw TEXT,
                    timestamp TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    direction TEXT,
                    entry REAL,
                    exit_price REAL,
                    result TEXT,
                    pnl_pct REAL,
                    timestamp TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    mode TEXT DEFAULT 'beginner',
                    deposit REAL DEFAULT 1000,
                    risk_pct REAL DEFAULT 1.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def save_signal(self, signal: Signal) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO signals (
                    symbol, exchange, direction, status, entry, stop_loss,
                    take_profits, risk_reward, quality_score, quality_grade,
                    confidence_pct, expected_move_pct, regime, timeframe,
                    reasons, raw, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.symbol,
                    signal.exchange,
                    signal.direction,
                    signal.status,
                    signal.entry,
                    signal.stop_loss,
                    json.dumps(signal.take_profits),
                    signal.risk_reward,
                    signal.quality_score,
                    signal.quality_grade,
                    signal.confidence_pct,
                    signal.expected_move_pct,
                    signal.regime,
                    signal.timeframe,
                    json.dumps(signal.reasons[:10]),
                    json.dumps(signal.to_dict()),
                    signal.timestamp.isoformat(),
                ),
            )
            conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.error(f"Failed to save signal: {e}")
            return -1
        finally:
            conn.close()

    def get_recent(self, limit: int = 50, symbol: str | None = None) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if symbol:
                cur = conn.execute(
                    "SELECT * FROM signals WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
                    (symbol.upper(), limit),
                )
            else:
                cur = conn.execute("SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_top(self, direction: str | None = None, limit: int = 20) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if direction:
                cur = conn.execute(
                    "SELECT * FROM signals WHERE direction = ? AND status = 'SIGNAL' ORDER BY quality_score DESC, confidence_pct DESC LIMIT ?",
                    (direction.upper(), limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM signals WHERE status = 'SIGNAL' ORDER BY quality_score DESC, confidence_pct DESC LIMIT ?",
                    (limit,),
                )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def save_outcome(self, symbol: str, direction: str, entry: float, exit_price: float, result: str, pnl_pct: float) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO outcomes (symbol, direction, entry, exit_price, result, pnl_pct, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (symbol, direction, entry, exit_price, result, pnl_pct, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_user_settings(self, user_id: int) -> dict:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            if row:
                return dict(row)
            # Default
            return {"user_id": user_id, "mode": "beginner", "deposit": 1000.0, "risk_pct": 1.0}
        finally:
            conn.close()

    def set_user_settings(self, user_id: int, **kwargs) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            # Upsert
            existing = self.get_user_settings(user_id)
            mode = kwargs.get("mode", existing.get("mode", "beginner"))
            deposit = kwargs.get("deposit", existing.get("deposit", 1000.0))
            risk_pct = kwargs.get("risk_pct", existing.get("risk_pct", 1.0))
            conn.execute(
                """
                INSERT INTO user_settings (user_id, mode, deposit, risk_pct)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET mode=?, deposit=?, risk_pct=?
                """,
                (user_id, mode, deposit, risk_pct, mode, deposit, risk_pct),
            )
            conn.commit()
        finally:
            conn.close()
