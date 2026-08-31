"""Per-user analysis settings persisted in SQLite (``v3_state`` keys).

Only a small, safe surface is user-editable: report mode, deposit for position
sizing, risk-per-trade %. Every value is bounded. Nothing here can enable
execution -- the engine has no order path at all.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from v3.config import SignalConfig
from v3.store import SignalStore

MAX_DEPOSIT_USD = 1_000_000.0
MAX_RISK_PCT = 5.0


class UserSettings:
    def __init__(self, mode: str = "beginner", deposit_usd: float = 1000.0, risk_per_trade_pct: float = 1.0) -> None:
        self.mode = mode if mode in ("beginner", "pro") else "beginner"
        self.deposit_usd = max(10.0, min(MAX_DEPOSIT_USD, float(deposit_usd)))
        self.risk_per_trade_pct = max(0.1, min(MAX_RISK_PCT, float(risk_per_trade_pct)))

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, cfg: SignalConfig | None = None) -> "UserSettings":
        cfg = cfg or SignalConfig()
        data = data or {}
        return cls(
            mode=str(data.get("mode") or ("pro" if not cfg.BEGINNER_MODE_DEFAULT else "beginner")),
            deposit_usd=float(data.get("deposit_usd") or cfg.DEFAULT_DEPOSIT_USD),
            risk_per_trade_pct=float(data.get("risk_per_trade_pct") or cfg.RISK_PER_TRADE_PCT),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "deposit_usd": round(self.deposit_usd, 2),
            "risk_per_trade_pct": round(self.risk_per_trade_pct, 3),
        }


class UserSettingsService:
    """Thin persisted store for per-user settings (thread-safe via SQLite lock)."""

    def __init__(self, store: SignalStore, cfg: SignalConfig | None = None) -> None:
        self.store = store
        self.cfg = cfg or SignalConfig()
        self._lock = threading.Lock()

    def _key(self, user_id: int) -> str:
        return f"user_settings:{user_id}"

    def get(self, user_id: int) -> UserSettings:
        raw = self.store.get_state(self._key(user_id), "")
        data: dict[str, Any] = {}
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                data = {}
        return UserSettings.from_dict(data, self.cfg)

    def save(self, user_id: int, settings: UserSettings) -> None:
        with self._lock:
            self.store.set_state(self._key(user_id), json.dumps(settings.to_dict(), ensure_ascii=False))

    def apply(self, user_id: int, key: str, value: str) -> UserSettings:
        settings = self.get(user_id)
        if key == "mode":
            settings.mode = "pro" if value == "pro" else "beginner"
        elif key == "deposit_usd":
            try:
                settings.deposit_usd = max(10.0, min(MAX_DEPOSIT_USD, float(value)))
            except (TypeError, ValueError):
                pass
        elif key == "risk_per_trade_pct":
            try:
                settings.risk_per_trade_pct = max(0.1, min(MAX_RISK_PCT, float(value)))
            except (TypeError, ValueError):
                pass
        self.save(user_id, settings)
        return settings
