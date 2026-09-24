from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)

BYBIT_TICKER_URL = "https://api.bybit.com/v5/market/tickers?category=linear"
BYBIT_ORDERBOOK_URL = "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=25"
BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit=100"
BYBIT_INSTRUMENT_URL = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"

SETTINGS_PATH = Path("data/pump_settings.json")


@dataclass
class PumpSettings:
    interval_seconds: int = 300
    threshold_pct: float = 5.0
    rsi_enabled: bool = False
    rsi_timeframes: tuple[str, ...] = ("15", "60", "240")
    rsi_overbought: float = 80.0
    rsi_oversold: float = 20.0
    day_filter_enabled: bool = False
    day_min_pct: float = 0.0
    signal_types: str = "BOTH"
    show_imbalance: bool = True
    show_listing: bool = False
    show_hashtag: bool = True
    show_volume: bool = True
    show_funding: bool = True

    @classmethod
    def load(cls) -> "PumpSettings":
        try:
            raw = json.loads(SETTINGS_PATH.read_text())
            defaults = asdict(cls())
            defaults.update(raw)
            defaults["rsi_timeframes"] = tuple(defaults["rsi_timeframes"])
            return cls(**defaults)
        except Exception:
            return cls()

    def save(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2))


@dataclass
class PumpSignal:
    symbol: str
    direction: str
    change_pct: float
    start_price: float
    current_price: float
    imbalance_buy_pct: float | None
    volume_24h: float
    funding_rate: float | None
    listing_ms: int | None
    rsi: dict[str, float]
    confirmations: int
    ts: float


class PumpScanner:
    """Bybit-style pump/dump detector.

    The primary trigger is deliberately simple and transparent:
    current price versus the rolling minimum/maximum inside the configured
    monitoring interval. Optional RSI and 24h filters are applied only when
    enabled. No orders are placed.
    """

    def __init__(self) -> None:
        self.settings = PumpSettings.load()
        self.session: aiohttp.ClientSession | None = None
        self.prices: dict[str, deque[tuple[float, float]]] = {}
        self.last_trigger: dict[tuple[str, str], float] = {}
        self.listings: dict[str, int] = {}
        self.running = False

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        await self._load_listings()

    async def stop(self) -> None:
        self.running = False
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None

    async def _get(self, url: str) -> dict:
        if not self.session:
            raise RuntimeError("PumpScanner is not started")
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if data.get("retCode", 0) != 0:
                raise RuntimeError(data.get("retMsg", "Bybit API error"))
            return data

    async def _load_listings(self) -> None:
        try:
            data = await self._get(BYBIT_INSTRUMENT_URL)
            for item in data.get("result", {}).get("list", []):
                if item.get("symbol", "").endswith("USDT"):
                    self.listings[item["symbol"]] = int(item.get("launchTime") or 0)
            log.info("Pump scanner loaded %d Bybit linear instruments", len(self.listings))
        except Exception:
            log.exception("Could not load Bybit listing dates")

    async def fetch_tickers(self) -> list[dict]:
        data = await self._get(BYBIT_TICKER_URL)
        return [
            x for x in data.get("result", {}).get("list", [])
            if x.get("symbol", "").endswith("USDT") and float(x.get("lastPrice") or 0) > 0
        ]

    def _trim(self, symbol: str, now: float) -> deque[tuple[float, float]]:
        q = self.prices.setdefault(symbol, deque())
        cutoff = now - self.settings.interval_seconds
        while q and q[0][0] < cutoff:
            q.popleft()
        return q

    async def update(self) -> list[PumpSignal]:
        now = time.time()
        tickers = await self.fetch_tickers()
        candidates: list[tuple[dict, str, float, float, float]] = []

        for t in tickers:
            symbol = t["symbol"]
            price = float(t["lastPrice"])
            q = self._trim(symbol, now)
            q.append((now, price))
            if len(q) < 2:
                continue

            low = min(p for _, p in q)
            high = max(p for _, p in q)
            pump_pct = (price - low) / low * 100 if low else 0
            dump_pct = (price - high) / high * 100 if high else 0

            direction = None
            change = 0.0
            start = price
            if pump_pct >= self.settings.threshold_pct:
                direction, change, start = "PUMP", pump_pct, low
            elif abs(dump_pct) >= self.settings.threshold_pct:
                direction, change, start = "DUMP", dump_pct, high

            if not direction:
                continue
            if self.settings.signal_types == "PUMP" and direction != "PUMP":
                continue
            if self.settings.signal_types == "DUMP" and direction != "DUMP":
                continue

            day_pct = float(t.get("price24hPcnt") or 0) * 100
            if self.settings.day_filter_enabled:
                if direction == "PUMP" and day_pct < self.settings.day_min_pct:
                    continue
                if direction == "DUMP" and day_pct > -self.settings.day_min_pct:
                    continue

            # One alert per direction until the price leaves the trigger zone.
            key = (symbol, direction)
            last = self.last_trigger.get(key, 0)
            if now - last < max(30, self.settings.interval_seconds):
                continue

            candidates.append((t, direction, change, start, day_pct))

        if not candidates:
            return []

        signals = await asyncio.gather(
            *(self._enrich(t, direction, change, start, day_pct) for t, direction, change, start, day_pct in candidates),
            return_exceptions=True,
        )
        out = []
        for candidate, result in zip(candidates, signals):
            if isinstance(result, Exception) or result is None:
                log.warning("Pump enrichment failed for %s: %s", candidate[0].get("symbol"), result)
                continue
            out.append(result)
            self.last_trigger[(result.symbol, result.direction)] = now
        return out

    async def _enrich(self, ticker: dict, direction: str, change: float, start: float, day_pct: float) -> PumpSignal | None:
        symbol = ticker["symbol"]
        rsi: dict[str, float] = {}
        if self.settings.rsi_enabled:
            values = await asyncio.gather(
                *(self._rsi(symbol, tf) for tf in self.settings.rsi_timeframes),
                return_exceptions=True,
            )
            for tf, value in zip(self.settings.rsi_timeframes, values):
                if isinstance(value, (int, float)):
                    rsi[tf] = float(value)

            if rsi:
                if direction == "PUMP" and not any(v >= self.settings.rsi_overbought for v in rsi.values()):
                    return None
                if direction == "DUMP" and not any(v <= self.settings.rsi_oversold for v in rsi.values()):
                    return None

        imbalance = None
        if self.settings.show_imbalance:
            imbalance = await self._imbalance(symbol)

        confirmations = 1
        if imbalance is not None:
            if (direction == "PUMP" and imbalance >= 50) or (direction == "DUMP" and imbalance < 50):
                confirmations += 1
        if self.settings.rsi_enabled and rsi:
            confirmations += 1
        if self.settings.day_filter_enabled:
            confirmations += 1

        return PumpSignal(
            symbol=symbol,
            direction=direction,
            change_pct=change,
            start_price=start,
            current_price=float(ticker["lastPrice"]),
            imbalance_buy_pct=imbalance,
            volume_24h=float(ticker.get("turnover24h") or 0),
            funding_rate=float(ticker.get("fundingRate") or 0) if ticker.get("fundingRate") else None,
            listing_ms=self.listings.get(symbol),
            rsi=rsi,
            confirmations=confirmations,
            ts=time.time(),
        )

    async def _imbalance(self, symbol: str) -> float | None:
        try:
            data = await self._get(BYBIT_ORDERBOOK_URL.format(symbol=symbol))
            levels = data.get("result", {}).get("b", []), data.get("result", {}).get("a", [])
            bid = sum(float(x[1]) * float(x[0]) for x in levels[0])
            ask = sum(float(x[1]) * float(x[0]) for x in levels[1])
            total = bid + ask
            return bid / total * 100 if total else None
        except Exception:
            return None

    async def _rsi(self, symbol: str, interval: str) -> float | None:
        try:
            data = await self._get(BYBIT_KLINE_URL.format(symbol=symbol, interval=interval))
            rows = data.get("result", {}).get("list", [])
            closes = [float(row[4]) for row in reversed(rows)]
            if len(closes) < 15:
                return None
            gains, losses = [], []
            for a, b in zip(closes[-15:-1], closes[-14:]):
                d = b - a
                gains.append(max(d, 0))
                losses.append(max(-d, 0))
            avg_gain = sum(gains) / 14
            avg_loss = sum(losses) / 14
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return 100 - 100 / (1 + rs)
        except Exception:
            return None

    async def scan_once(self) -> list[PumpSignal]:
        return await self.update()

    def format_signal(self, s: PumpSignal) -> str:
        icon = "🟢" if s.direction == "PUMP" else "🔴"
        name = s.symbol.removesuffix("USDT")
        lines = [
            f"{icon} {name} Bybit #{name.lower()}",
            f"{'Pump' if s.direction == 'PUMP' else 'Dump'}: {s.change_pct:+.2f}% ({s.start_price:.8g} → {s.current_price:.8g})",
        ]
        if self.settings.show_imbalance and s.imbalance_buy_pct is not None:
            sell = 100 - s.imbalance_buy_pct
            book_icon = "🟢" if s.imbalance_buy_pct >= 50 else "🔴"
            lines.append(f"📉 Дисбаланс: {book_icon} ({s.imbalance_buy_pct:.1f}% / {sell:.1f}%)")
        if self.settings.show_volume:
            lines.append(f"📈 Объём 24ч: {self._fmt_volume(s.volume_24h)} USDT")
        if self.settings.show_funding and s.funding_rate is not None:
            lines.append(f"💸 Funding: {s.funding_rate * 100:.4f}%")
        if self.settings.show_listing and s.listing_ms:
            age_days = max(0, int((time.time() * 1000 - s.listing_ms) / 86_400_000))
            lines.append(f"🗓 Листинг: {age_days} дн.")
        if s.rsi:
            lines.append("📊 RSI: " + " | ".join(f"{tf}={v:.1f}" for tf, v in s.rsi.items()))
        lines.append(f"📡 Сигнал: {s.confirmations}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_volume(value: float) -> str:
        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.1f}B"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K"
        return f"{value:.0f}"
