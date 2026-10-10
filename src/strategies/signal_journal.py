from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)

TICKER_URL = "https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
KLINE_URL = "https://api.bybit.com/v5/market/kline"


class SignalJournal:
    """Private local journal for actionable signals actually delivered to Telegram.

    SQLite is intentionally kept out of Git. On ephemeral hosting the file can be
    lost on redeploy; set SIGNAL_JOURNAL_PATH to a persistent mounted path or use
    a future durable database adapter for retention across deploys.
    """

    HORIZONS = {"15m": 15 * 60, "1h": 60 * 60, "4h": 4 * 60 * 60, "24h": 24 * 60 * 60}

    def __init__(self) -> None:
        default_path = Path("data/.signal_journal/signals.sqlite3")
        self.path = Path(os.getenv("SIGNAL_JOURNAL_PATH", str(default_path)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS recommendations (
                id TEXT PRIMARY KEY,
                recommended_at REAL NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                source TEXT NOT NULL,
                entry_price REAL NOT NULL,
                scanner_price REAL,
                quality_score INTEGER,
                change_pct REAL,
                entry_low REAL,
                entry_high REAL,
                stop_price REAL,
                tp1 REAL,
                tp2 REAL,
                confirmation_verdict TEXT,
                confirmation_score INTEGER,
                confirmation_metrics TEXT NOT NULL,
                confirmation_reasons TEXT NOT NULL,
                confirmation_warnings TEXT NOT NULL,
                signal_snapshot TEXT NOT NULL,
                outcomes TEXT NOT NULL DEFAULT '{}',
                last_price REAL,
                last_checked_at REAL,
                status TEXT NOT NULL DEFAULT 'tracking'
            );
            CREATE INDEX IF NOT EXISTS idx_recommendations_time
                ON recommendations(recommended_at DESC);
            CREATE INDEX IF NOT EXISTS idx_recommendations_tracking
                ON recommendations(status, recommended_at);
        """)
        self.db.commit()
        self.session: aiohttp.ClientSession | None = None
        self._evaluation_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=12)
            )

    async def stop(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        self.db.commit()
        self.db.close()

    @staticmethod
    def _positive(value) -> float | None:
        try:
            number = float(value)
            return number if number > 0 else None
        except (TypeError, ValueError):
            return None

    def record_recommendation(self, signal, confirmation, source: str) -> str | None:
        """Persist a recommendation only after its Telegram entry message was sent."""
        metrics = dict(getattr(confirmation, "metrics", {}) or {})
        entry = self._positive(metrics.get("live_price"))
        scanner_price = self._positive(getattr(signal, "current_price", None))
        if entry is None:
            entry = scanner_price
        if entry is None:
            log.warning("Journal skipped %s: no valid entry price", getattr(signal, "symbol", "?"))
            return None

        now = time.time()
        signal_snapshot = {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "change_pct": signal.change_pct,
            "start_price": signal.start_price,
            "current_price": signal.current_price,
            "imbalance_buy_pct": signal.imbalance_buy_pct,
            "volume_24h": signal.volume_24h,
            "funding_rate": signal.funding_rate,
            "listing_ms": signal.listing_ms,
            "rsi": signal.rsi,
            "confirmations": signal.confirmations,
            "trade_action": signal.trade_action,
            "trade_reason": signal.trade_reason,
            "entry_low": signal.entry_low,
            "entry_high": signal.entry_high,
            "stop_price": signal.stop_price,
            "tp1": signal.tp1,
            "tp2": signal.tp2,
            "signal_ts": signal.ts,
            "quality_score": signal.quality_score,
        }
        row_id = uuid.uuid4().hex
        self.db.execute(
            """INSERT INTO recommendations (
                id, recommended_at, symbol, direction, source, entry_price,
                scanner_price, quality_score, change_pct, entry_low, entry_high,
                stop_price, tp1, tp2, confirmation_verdict, confirmation_score,
                confirmation_metrics, confirmation_reasons, confirmation_warnings,
                signal_snapshot, last_price, last_checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row_id, now, signal.symbol, signal.direction, source, entry,
                scanner_price, signal.quality_score, signal.change_pct,
                signal.entry_low, signal.entry_high, signal.stop_price,
                signal.tp1, signal.tp2, confirmation.verdict, confirmation.score,
                json.dumps(metrics, ensure_ascii=False, default=str),
                json.dumps(confirmation.reasons, ensure_ascii=False, default=str),
                json.dumps(confirmation.warnings, ensure_ascii=False, default=str),
                json.dumps(signal_snapshot, ensure_ascii=False, default=str),
                entry, now,
            ),
        )
        self.db.commit()
        log.info(
            "Signal journal recorded id=%s symbol=%s direction=%s source=%s entry=%.10g",
            row_id, signal.symbol, signal.direction, source, entry,
        )
        log.info(
            "SIGNAL_JOURNAL_RECORD id=%s recommended_at=%.3f snapshot=%s confirmation=%s",
            row_id, now, json.dumps(signal_snapshot, ensure_ascii=False, separators=(",", ":"), default=str),
            json.dumps({
                "verdict": confirmation.verdict,
                "score": confirmation.score,
                "metrics": metrics,
                "reasons": confirmation.reasons,
                "warnings": confirmation.warnings,
            }, ensure_ascii=False, separators=(",", ":"), default=str),
        )
        return row_id

    async def evaluate_due(self) -> None:
        """Resolve 15m/1h/4h/24h outcomes from Bybit historical candles."""
        if not self.session or self.session.closed:
            return
        async with self._evaluation_lock:
            rows = self.db.execute(
                "SELECT * FROM recommendations WHERE status='tracking' ORDER BY recommended_at ASC LIMIT 100"
            ).fetchall()
            now = time.time()
            for row in rows:
                try:
                    outcomes = json.loads(row["outcomes"] or "{}")
                    for label, seconds in self.HORIZONS.items():
                        if label in outcomes or now < row["recommended_at"] + seconds + 65:
                            continue
                        outcome = await self._historical_outcome(row, seconds)
                        if outcome is not None:
                            outcomes[label] = outcome
                            log.info(
                                "SIGNAL_JOURNAL_OUTCOME id=%s symbol=%s direction=%s horizon=%s result=%s",
                                row["id"], row["symbol"], row["direction"], label,
                                json.dumps(outcome, ensure_ascii=False, separators=(",", ":")),
                            )
                            self.db.execute(
                                "UPDATE recommendations SET outcomes=?, last_price=?, last_checked_at=? WHERE id=?",
                                (
                                    json.dumps(outcomes, ensure_ascii=False),
                                    outcome.get("end_price", row["last_price"]),
                                    now,
                                    row["id"],
                                ),
                            )
                            self.db.commit()
                    if len(outcomes) == len(self.HORIZONS):
                        self.db.execute(
                            "UPDATE recommendations SET status='complete' WHERE id=?",
                            (row["id"],),
                        )
                        self.db.commit()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning(
                        "Signal journal outcome update failed id=%s: %s",
                        row["id"], type(exc).__name__,
                    )

    async def _historical_outcome(self, row: sqlite3.Row, horizon_seconds: int) -> dict | None:
        if not self.session:
            return None
        interval = "5" if horizon_seconds >= 24 * 60 * 60 else "1"
        interval_ms = int(interval) * 60 * 1000
        start = int(row["recommended_at"] * 1000)
        end = int((row["recommended_at"] + horizon_seconds) * 1000)
        # Skip the candle that began before the recommendation to avoid using pre-entry extrema.
        start = ((start + interval_ms - 1) // interval_ms) * interval_ms
        params = {
            "category": "linear",
            "symbol": row["symbol"],
            "interval": interval,
            "start": str(start),
            "end": str(end),
            "limit": "1000" if interval == "1" else "300",
        }
        async with self.session.get(KLINE_URL, params=params) as resp:
            resp.raise_for_status()
            payload = await resp.json()
        if payload.get("retCode", 0) != 0:
            raise RuntimeError(str(payload.get("retMsg", "Bybit kline error")))
        raw_rows = payload.get("result", {}).get("list", [])
        candles = []
        for item in raw_rows:
            try:
                stamp = int(item[0])
                if start <= stamp < end:
                    candles.append({
                        "ts": stamp,
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                    })
            except (IndexError, TypeError, ValueError):
                continue
        if not candles:
            return None
        candles.sort(key=lambda x: x["ts"])
        entry = float(row["entry_price"])
        high = max(x["high"] for x in candles)
        low = min(x["low"] for x in candles)
        end_price = candles[-1]["close"]
        is_long = row["direction"] in {"PUMP", "LONG"}
        favorable = max(0.0, ((high - entry) / entry * 100) if is_long else ((entry - low) / entry * 100))
        adverse = max(0.0, ((entry - low) / entry * 100) if is_long else ((high - entry) / entry * 100))
        return {
            "horizon_seconds": horizon_seconds,
            "candles": len(candles),
            "start_candle_at": candles[0]["ts"] / 1000,
            "end_candle_at": candles[-1]["ts"] / 1000,
            "end_price": end_price,
            "return_pct": ((end_price - entry) / entry * 100) * (1 if is_long else -1),
            "mfe_pct": favorable,
            "mae_pct": adverse,
            "high": high,
            "low": low,
            "source": "Bybit historical OHLC; first partial candle excluded",
        }

    def summary(self) -> str:
        total = self.db.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
        complete = self.db.execute("SELECT COUNT(*) FROM recommendations WHERE status='complete'").fetchone()[0]
        rows = self.db.execute(
            "SELECT * FROM recommendations ORDER BY recommended_at DESC LIMIT 8"
        ).fetchall()
        lines = [
            "🗂 Журнал рекомендаций",
            f"Всего записано: {total} · Полностью созрели 24ч: {complete}",
            "Сохраняются только реально отправленные подтверждённые LONG/SHORT.",
        ]
        if not rows:
            lines.append("Пока нет записей. Журнал начнёт собирать историю после следующей отправленной рекомендации.")
            return "\n".join(lines)
        for row in rows:
            outcomes = json.loads(row["outcomes"] or "{}")
            dt = time.strftime("%d.%m %H:%M UTC", time.gmtime(row["recommended_at"]))
            side = "LONG" if row["direction"] in {"PUMP", "LONG"} else "SHORT"
            lines.append(
                f"\n{dt} · {row['symbol']} {side} · вход {row['entry_price']:.8g} · score {row['quality_score']} · realtime {row['confirmation_score']}/100"
            )
            if outcomes:
                for label in ("15m", "1h", "4h", "24h"):
                    o = outcomes.get(label)
                    if o:
                        lines.append(
                            f"  {label}: итог {o['return_pct']:+.2f}% · MFE {o['mfe_pct']:+.2f}% · MAE {o['mae_pct']:+.2f}%"
                        )
            else:
                lines.append("  Результат ещё собирается.")
        return "\n".join(lines)[:3900]

    def export_recent_json(self, limit: int = 20) -> str:
        rows = self.db.execute(
            "SELECT * FROM recommendations ORDER BY recommended_at DESC LIMIT ?",
            (max(1, min(limit, 20)),),
        ).fetchall()
        exported = []
        for row in rows:
            item = dict(row)
            for key in ("confirmation_metrics", "confirmation_reasons", "confirmation_warnings", "signal_snapshot", "outcomes"):
                try:
                    item[key] = json.loads(item[key] or "{}")
                except (TypeError, json.JSONDecodeError):
                    pass
            exported.append(item)
        return json.dumps(exported, ensure_ascii=False, indent=2, default=str)
