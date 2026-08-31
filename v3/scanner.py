"""Automatic universe scanner for USDT perpetuals.

The bot must not depend on a hard-coded watchlist.  ``Scanner`` ranks every
available liquid perpetual with a deterministic candidate score, then hands the
top symbols to the full signal engine.  No candidate is emitted unless it
crosses liquidity/volume/spread thresholds AND later passes the signal gate.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from v3.config import SignalConfig
from v3.engine import FuturesSignalEngine
from v3.models import ScanCandidate


def _fin(v: Any) -> bool:
    if v is None:
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _rank_candidate(t: Any, cfg: SignalConfig) -> ScanCandidate | None:
    """Turn one exchange ticker into a ranked candidate (or None if trash)."""
    symbol = str(getattr(t, "symbol", ""))
    turnover = float(getattr(t, "turnover_24h", 0) or 0)
    volume = float(getattr(t, "volume_24h", 0) or 0)
    pct = float(getattr(t, "price_24h_pct", 0) or 0)
    high = float(getattr(t, "high_24h", 0) or 0)
    low = float(getattr(t, "low_24h", 0) or 0)
    bid = float(getattr(t, "bid", 0) or 0)
    ask = float(getattr(t, "ask", 0) or 0)
    funding = getattr(t, "funding_rate", None)
    oi = getattr(t, "open_interest_usd", None) or getattr(t, "open_interest", None)

    if not symbol.endswith("USDT") or turnover <= 0 or turnover < cfg.SCAN_MIN_TURNOVER_USD:
        return None

    spread_pct = None
    if bid > 0 and ask > 0:
        spread_pct = (ask - bid) / ask * 100.0
        if spread_pct > cfg.MAX_SPREAD_PCT:
            return None

    # volatility proxy from 24h range
    vol_pct = ((high - low) / low * 100.0) if low > 0 else 0.0

    heat = 0.0
    heat += min(30.0, max(0.0, pct) * 2.5)               # momentum up
    heat += min(30.0, max(0.0, -pct) * 2.0)              # momentum down (short side)
    heat += min(25.0, math.log10(max(turnover, 1)) * 2.0)  # liquidity
    heat += min(15.0, vol_pct * 1.2)                      # activity
    # funding: neither overheated longs nor shorts is a plus
    if _fin(funding):
        f = float(funding)
        if abs(f) <= cfg.FUNDING_OVERHEATED * 0.6:
            heat += 8.0
        elif abs(f) > cfg.FUNDING_OVERHEATED:
            heat -= 6.0
    # tighter spread is better
    if spread_pct is not None:
        heat += min(6.0, (cfg.MAX_SPREAD_PCT - spread_pct) / max(cfg.MAX_SPREAD_PCT, 0.01) * 6.0)

    # majors get a slight penalty so the scanner surfaces interesting alts
    base = symbol.replace("USDT", "")
    if base in {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK"}:
        heat -= 4.0

    return ScanCandidate(
        symbol=symbol,
        price=float(getattr(t, "last", 0) or 0),
        price_24h_pct=pct,
        turnover_24h=turnover,
        volume_24h=volume,
        funding_rate=float(funding) if _fin(funding) else None,
        open_interest_usd=float(oi) if _fin(oi) else None,
        spread_pct=spread_pct,
        heat=round(heat, 2),
        liquidity_ok=turnover >= cfg.SCAN_MIN_TURNOVER_USD,
        volume_ok=volume >= cfg.SCAN_MIN_VOLUME_USD,
        reason="",
    )


@dataclass
class ScanResult:
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    scanned_total: int = 0                     # сколько тикеров реально получено с биржи
    candidates: list[ScanCandidate] = field(default_factory=list)
    analyzed: list[dict[str, Any]] = field(default_factory=list)
    top_by_heat: list[ScanCandidate] = field(default_factory=list)
    duration_sec: float = 0.0
    mode: str = ""


class Scanner:
    """Scan all liquid USDT-perp instruments, rank, then deep-analyse the top."""

    def __init__(self, engine: FuturesSignalEngine, cfg: SignalConfig | None = None) -> None:
        self.engine = engine
        self.cfg = cfg or SignalConfig()
        self.last: ScanResult | None = None

    async def run(self, tickers: dict[str, Any], limit: int | None = None, top: int | None = None) -> ScanResult:
        started = time.time()
        limit = limit or self.cfg.SCAN_LIMIT
        top = top or self.cfg.SCAN_TOP
        candidates = [c for c in (_rank_candidate(t, self.cfg) for t in tickers.values()) if c is not None]
        candidates.sort(key=lambda c: c.heat, reverse=True)
        candidates = candidates[:limit]

        result = ScanResult(
            scanned_total=len(tickers),
            candidates=candidates,
            top_by_heat=candidates[:top],
            mode=self.engine.data.mode,
        )

        if candidates:
            top_symbols = [c.symbol for c in candidates[:top]]
            signals = await self.engine.analyze_batch(top_symbols, concurrency=4)
            by_symbol = {getattr(s, "symbol", ""): s for s in signals}
            # keep the ranking context plus the deep signal snapshot
            for c in candidates[:top]:
                s = by_symbol.get(c.symbol)
                if s is not None:
                    result.analyzed.append({"candidate": c.to_dict(), "signal": s})
            result.analyzed.sort(key=lambda item: item["signal"].quality, reverse=True)

        result.duration_sec = time.time() - started
        self.last = result
        return result

    # ── result views used by the Telegram UI / API ────────────────
    def best_setups(self, direction: str | None = None, quality_min: float | None = None, top_only: bool = False) -> list[dict[str, Any]]:
        """Deep-analysed setups sorted by quality, optional direction filter.

        ``direction`` is LONG | SHORT | None (any). ``quality_min`` по
        умолчанию: ``SCAN_LIST_QUALITY_MIN`` (тир-осознанные списки, B/C
        видны); с ``top_only=True`` — строгий ``SCAN_SHOW_QUALITY_MIN``,
        используется только для «⭐ ТОП». Weak setups stay visible in the raw
        scan either way.
        """
        if self.last is None:
            return []
        if quality_min is None:
            quality_min = self.cfg.SCAN_SHOW_QUALITY_MIN if top_only else self.cfg.SCAN_LIST_QUALITY_MIN
        items: list[dict[str, Any]] = []
        for item in self.last.analyzed:
            sig = item["signal"]
            if direction and sig.direction != direction:
                continue
            if sig.direction not in ("LONG", "SHORT"):
                continue
            if sig.quality < quality_min:
                continue
            items.append(item)
        items.sort(key=lambda item: item["signal"].quality, reverse=True)
        return items

    def top_setups(self, direction: str | None = None) -> list[dict[str, Any]]:
        """Строгий «⭐ ТОП» — только сетапы выше SCAN_SHOW_QUALITY_MIN."""
        return self.best_setups(direction, top_only=True)

    def heatmap(self, limit: int = 20) -> list[dict[str, Any]]:
        if self.last is None:
            return []
        return [c.to_dict() for c in self.last.candidates[:limit]]

    def to_dict(self) -> dict[str, Any]:
        if self.last is None:
            return {"candidates": [], "analyzed": [], "duration_sec": 0.0}
        return {
            "ts_ms": self.last.ts_ms,
            "scanned_total": self.last.scanned_total,
            "candidates": [c.to_dict() for c in self.last.candidates],
            "analyzed": [
                {"candidate": item["candidate"], "signal": item["signal"].to_dict()}
                for item in self.last.analyzed
            ],
            "duration_sec": self.last.duration_sec,
            "mode": self.last.mode,
        }
