"""Automatic universe scanner for USDT perpetuals.

The bot must not depend on a hard-coded watchlist.  ``Scanner`` ranks every
available liquid perpetual with a deterministic candidate score, then hands the
top symbols to the full signal engine.  No candidate is emitted unless it
crosses liquidity/volume/spread thresholds AND later passes the signal gate.

Раунд 4 — «ловить до разгона, а не после»:
  * cross-sectional RS (vs BTC и vs медианы вселенной) вместо «24h % как есть»;
  * RVOL / squeeze-release / консолидация / близость к экстремуму диапазона —
    детектор ``v3/analysis/emergence.py`` (признак ранжирования, не гейт);
  * анти-chase: штраф, если монета УЖЕ у вершины/дна после большого хода;
  * диверсификация корзины по (RS, зоне диапазона) — чтобы «топ-20» не был
    20 копиями одного движения (полная корреляционная кластеризация — P2).
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any

from v3.analysis.emergence import detect_emergence
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


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _rank_candidate(
    t: Any,
    cfg: SignalConfig,
    btc_pct: float | None = None,
    median_pct: float | None = None,
) -> ScanCandidate | None:
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

    # ── позиция в 24h-диапазоне + относительная сила (раунд 4) ──
    dpos = 0.5
    if high > low > 0:
        dpos = _clamp((float(getattr(t, "last", 0) or 0) - low) / (high - low), 0.0, 1.0)
    rs24 = pct - (btc_pct if btc_pct is not None else 0.0)
    universe_rel = pct - (median_pct if median_pct is not None else 0.0)

    heat = 0.0
    # моментум — меньше веса, чем раньше (было 30/2.5 => 45% heat = chase)
    heat += min(18.0, max(0.0, pct) * 1.8)                 # momentum up
    heat += min(16.0, max(0.0, -pct) * 1.6)                # momentum down (short side)
    heat += min(25.0, math.log10(max(turnover, 1)) * 2.0)  # liquidity
    heat += min(15.0, vol_pct * 1.2)                       # activity
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

    # ── раунд 4: относительная сила + «ещё есть место» (ранний отбор) ──
    if rs24 >= 3.0 and 0.30 <= dpos <= 0.85:
        heat += 8.0          # сильнее BTC, но не у вершины — только намечается
    elif rs24 <= -3.0 and 0.15 <= dpos <= 0.70:
        heat += 6.0          # слабее BTC и не у дна — кандидат на разворот
    if 0.30 <= dpos <= 0.75:
        heat += 5.0          # середина диапазона = «есть место» для движения

    # анти-chase: уже у экстремума после большого хода → это «после», не «до».
    # Строгий штраф: momentum/activity у вершины — это вчерашняя новость.
    if dpos >= 0.95 and pct >= 8.0:
        heat -= 18.0
    elif dpos <= 0.05 and pct <= -8.0:
        heat -= 15.0
    elif dpos >= 0.90 and pct >= 12.0:
        heat -= 8.0          # почти у вершины после сильного хода — уже не «намечается»
    elif dpos <= 0.10 and pct <= -12.0:
        heat -= 8.0

    # мажоры — мягкий штраф по конфигу (watchlist), без хардкода
    base = symbol.replace("USDT", "")
    if base in cfg.watchlist or symbol in cfg.watchlist:
        heat -= cfg.SCAN_MAJOR_PENALTY
    if universe_rel <= -15.0:
        heat -= 6.0            # монета сильно хуже рынка — не «намечается», а умирает

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
        dpos=round(dpos, 3),
        rs24=round(rs24, 3),
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

    async def _instrument_map(self) -> dict[str, Any]:
        """Инструменты биржи → метаданные (возраст листинга). Ошибки не роняют скан."""
        data = getattr(self.engine, "data", None)
        if data is None or not hasattr(data, "instruments"):
            return {}
        try:
            rows = await data.instruments()
            return {getattr(x, "symbol", "").upper(): x for x in rows}
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _age_days(inst: Any) -> float | None:
        launch = getattr(inst, "launch_time_ms", None) or getattr(inst, "launchTime_ms", None)
        if not launch or launch <= 0:
            return None
        return max(0.0, (time.time() * 1000 - float(launch)) / 86_400_000.0)

    async def run(self, tickers: dict[str, Any], limit: int | None = None, top: int | None = None) -> ScanResult:
        started = time.time()
        limit = limit or self.cfg.SCAN_LIMIT
        top = top or self.cfg.SCAN_TOP

        btc_pct = None
        btc_t = tickers.get("BTCUSDT")
        if btc_t is not None and _fin(getattr(btc_t, "price_24h_pct", None)):
            btc_pct = float(btc_t.price_24h_pct)

        raw = [_rank_candidate(t, self.cfg, btc_pct) for t in tickers.values()]
        candidates = [c for c in raw if c is not None]
        if candidates:
            median_pct = float(sorted(c.price_24h_pct for c in candidates)[len(candidates) // 2])
            # пересчёт с кросс-секционной медианой (RS относительно вселенной)
            candidates = [
                _rank_candidate(tickers.get(c.symbol), self.cfg, btc_pct, median_pct)
                or c
                for c in candidates
            ]
        candidates.sort(key=lambda c: c.heat, reverse=True)
        candidates = candidates[:limit]

        # возраст листинга: свежие монеты — отдельный режим, помечаем честно
        instr_map = await self._instrument_map()
        for c in candidates:
            inst = instr_map.get(c.symbol)
            if inst is not None:
                c.age_days = self._age_days(inst)
                c.fresh_listing = c.age_days is not None and c.age_days < self.cfg.SCAN_AGE_DAYS_MIN
                if c.fresh_listing:
                    c.reason = f"недавний листинг ({c.age_days:.0f} дн.) — отдельный режим"

        result = ScanResult(
            scanned_total=len(tickers),
            candidates=candidates,
            top_by_heat=candidates[:top],
            mode=self.engine.data.mode if hasattr(self.engine, "data") else "",
        )

        # ── emergence: «намечается движение» на 1h свечах ────────
        ignitions: dict[str, dict[str, Any]] = {}
        if self.cfg.SCAN_EMERGENCE_ENABLED and candidates:
            pool = candidates[: self.cfg.SCAN_EMERGENCE_POOL]
            ignitions = await self._emergence_pool(pool, btc_pct)
            for c in candidates:
                e = ignitions.get(c.symbol)
                if e is not None:
                    boost = float(e.get("ignition", 0.0)) * self.cfg.SCAN_EMERGENCE_BOOST
                    c.heat = round(c.heat + boost, 2)
                    c.ignition = float(e.get("ignition", 0.0))
                    c.early_direction = str(e.get("early_direction", "FLAT"))
                    c.emergence_note = " | ".join(e.get("notes", [])[:3])
            candidates.sort(key=lambda c: c.heat, reverse=True)
            result.candidates = candidates
            result.top_by_heat = candidates[:top]

        # ── диверсификация: не 20 копий одного движения ──────────
        pool_all = candidates[: max(top * 3, self.cfg.SCAN_EMERGENCE_POOL)]
        selected = self._diversify(pool_all, top)

        if selected:
            top_symbols = [c.symbol for c in selected]
            signals = await self.engine.analyze_batch(top_symbols, concurrency=4)
            by_symbol = {getattr(s, "symbol", ""): s for s in signals}
            for c in selected:
                s = by_symbol.get(c.symbol)
                if s is None:
                    continue
                e = ignitions.get(c.symbol)
                if e is not None:
                    s.features["emergence"] = e
                    s.reasons = (s.reasons + [r for r in e.get("notes", []) if r])[:10]
                result.analyzed.append({"candidate": c.to_dict(), "signal": s})
            result.analyzed.sort(key=lambda item: item["signal"].quality, reverse=True)

        result.duration_sec = time.time() - started
        self.last = result
        return result

    async def _emergence_pool(self, pool: list[ScanCandidate], btc_pct: float | None) -> dict[str, dict[str, Any]]:
        """RVOL/squeeze/консолидация по 1h свечам для кандидатов (только реальные данные)."""
        data = getattr(self.engine, "data", None)
        if data is None or not hasattr(data, "klines") or not hasattr(data, "tickers"):
            return {}

        sem = asyncio.Semaphore(6)

        async def _one(c: ScanCandidate) -> tuple[str, dict[str, Any] | None]:
            async with sem:
                try:
                    df = await data.klines(c.symbol, "1h", self.cfg.SCAN_EMERGENCE_BARS)
                    t = (await data.tickers([c.symbol])).get(c.symbol)
                    if df is None or len(df) < 30:
                        return c.symbol, None
                    e = detect_emergence(
                        df,
                        price_24h_pct=c.price_24h_pct,
                        high_24h=float(getattr(t, "high_24h", 0) or 0) or None,
                        low_24h=float(getattr(t, "low_24h", 0) or 0) or None,
                        btc_24h_pct=btc_pct,
                        oi_delta_pct=c.oi_delta_pct,
                        funding_rate=c.funding_rate,
                        cfg=self.cfg,
                    )
                    return c.symbol, e.to_dict()
                except Exception:  # noqa: BLE001
                    return c.symbol, None

        rows = await asyncio.gather(*(_one(c) for c in pool))
        return {sym: d for sym, d in rows if d is not None}

    # ── diversity-корзина (лёгкая эвристическая версия) ─────────
    def _diversify(self, candidates: list[ScanCandidate], top: int) -> list[ScanCandidate]:
        """Не больше ``DIVERSITY_MAX_PER_CLUSTER`` кандидатов на «корзину».

        Корзина = похожие (RS, позиция в диапазоне): так мы не отправим в
        Stage 2 десять копий одного движения. Полная корреляционная
        кластеризация по 1h-доходностям — этап P2 (см. docs).
        """
        if len(candidates) <= top:
            return candidates
        clusters: list[list[ScanCandidate]] = []
        for c in candidates:
            placed = False
            for members in clusters:
                if members and abs(members[0].rs24 - c.rs24) < 3.0 and abs(members[0].dpos - c.dpos) < 0.2:
                    # кластер найден: если в нём уже есть лимит — кандидат НЕ
                    # заводит новый кластер, а выбывает (иначе diversity не работает)
                    if len(members) < max(1, self.cfg.DIVERSITY_MAX_PER_CLUSTER):
                        members.append(c)
                    placed = True
                    break
            if not placed:
                clusters.append([c])
        for members in clusters:
            members.sort(key=lambda c: c.heat, reverse=True)
        out: list[ScanCandidate] = []
        while len(out) < top and clusters:
            again: list[list[ScanCandidate]] = []
            for members in clusters:
                if not members:
                    continue
                out.append(members.pop(0))
                if members:
                    again.append(members)
                if len(out) >= top:
                    break
            clusters = again
        return out

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

    def emerging(self, ignition_min: float | None = None) -> list[dict[str, Any]]:
        """«⚡ НАМЕЧАЕТСЯ»: кандидаты с высоким ignition (ранний отбор)."""
        if self.last is None:
            return []
        threshold = ignition_min if ignition_min is not None else self.cfg.EMERGENCE_IGNITION_MIN
        items = [
            item for item in self.last.analyzed
            if (item.get("candidate") or {}).get("ignition", 0.0) >= threshold
        ]
        items.sort(key=lambda item: item["candidate"]["ignition"], reverse=True)
        return items

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
