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
    threshold_pct: float = 3.0
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
    confirm_enabled: bool = True
    confirmation_timeframe: str = "1"
    confirmation_candles: int = 3
    risk_reward_1: float = 1.5
    risk_reward_2: float = 2.5
    min_signal_score: int = 60

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
    trade_action: str
    trade_reason: str
    entry_low: float | None
    entry_high: float | None
    stop_price: float | None
    tp1: float | None
    tp2: float | None
    ts: float
    quality_score: int


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
        # Do not block Telegram startup on historical seeding. The live monitor
        # must start immediately; history is filled in the background.
        self.running = True
        asyncio.create_task(self._seed_history(), name="pump-history-seed")

    async def _seed_history(self) -> None:
        """Seed the rolling window from Bybit 1m candles so a fresh restart can detect moves immediately."""
        try:
            tickers = await self.fetch_tickers()
            # Seed the most liquid symbols first, then continue through the
            # complete Bybit USDT universe. This gets useful coverage quickly
            # without turning startup into a long blocking operation.
            symbols = [
                t["symbol"]
                for t in sorted(
                    tickers,
                    key=lambda x: float(x.get("turnover24h") or 0),
                    reverse=True,
                )
            ]
            sem = asyncio.Semaphore(12)
            async def seed(symbol: str) -> None:
                async with sem:
                    try:
                        limit = min(100, max(10, self.settings.interval_seconds // 60 + 3))
                        data = await self._get(BYBIT_KLINE_URL.format(symbol=symbol, interval="1", limit=limit))
                        rows = data.get("result", {}).get("list", [])
                        q = self.prices.setdefault(symbol, deque())
                        for row in reversed(rows):
                            ts = float(row[0]) / 1000.0
                            close = float(row[4])
                            if close > 0:
                                q.append((ts, close))
                        self._trim(symbol, time.time())
                    except Exception:
                        pass
            await asyncio.gather(*(seed(s) for s in symbols))
            log.info(
                "Pump scanner seeded price history for %d/%d Bybit symbols",
                len(self.prices),
                len(symbols),
            )
        except Exception:
            log.exception("Could not seed pump scanner history")

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
        if not self.running:
            return []
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
            values = await asyncio.gather(*(self._rsi(symbol, tf) for tf in self.settings.rsi_timeframes), return_exceptions=True)
            for tf, value in zip(self.settings.rsi_timeframes, values):
                if isinstance(value, (int, float)):
                    rsi[tf] = float(value)
            if rsi:
                if direction == "PUMP" and not any(v >= self.settings.rsi_overbought for v in rsi.values()):
                    return None
                if direction == "DUMP" and not any(v <= self.settings.rsi_oversold for v in rsi.values()):
                    return None
        imbalance = await self._imbalance(symbol) if self.settings.show_imbalance else None
        confirmations = 1
        if imbalance is not None and ((direction == "PUMP" and imbalance >= 55) or (direction == "DUMP" and imbalance <= 45)):
            confirmations += 1
        if self.settings.rsi_enabled and rsi:
            confirmations += 1
        if self.settings.day_filter_enabled:
            confirmations += 1
        action, reason, entry_low, entry_high, stop, tp1, tp2 = await self._trade_confirmation(
            symbol, direction, float(ticker["lastPrice"]), imbalance
        )
        score = 20
        score += min(20, int(max(0.0, change / max(self.settings.threshold_pct, 0.1)) * 10))
        if imbalance is not None:
            if direction == "PUMP":
                score += 20 if imbalance >= 60 else 15 if imbalance >= 55 else 8 if imbalance >= 50 else 0
            else:
                score += 20 if imbalance <= 40 else 15 if imbalance <= 45 else 8 if imbalance <= 50 else 0
        if action in {"LONG", "SHORT"}:
            score += 30
        if self.settings.rsi_enabled and rsi:
            if direction == "PUMP" and any(v >= self.settings.rsi_overbought for v in rsi.values()):
                score += 10
            elif direction == "DUMP" and any(v <= self.settings.rsi_oversold for v in rsi.values()):
                score += 10
        if (direction == "PUMP" and day_pct > 0) or (direction == "DUMP" and day_pct < 0):
            score += 5
        score = min(100, score)
        return PumpSignal(
            symbol=symbol, direction=direction, change_pct=change, start_price=start,
            current_price=float(ticker["lastPrice"]), imbalance_buy_pct=imbalance,
            volume_24h=float(ticker.get("turnover24h") or 0),
            funding_rate=float(ticker.get("fundingRate") or 0) if ticker.get("fundingRate") else None,
            listing_ms=self.listings.get(symbol), rsi=rsi, confirmations=confirmations,
            trade_action=action, trade_reason=reason, entry_low=entry_low, entry_high=entry_high,
            stop_price=stop, tp1=tp1, tp2=tp2, ts=time.time(), quality_score=score,
        )

    async def _trade_confirmation(self, symbol: str, direction: str, price: float, imbalance: float | None):
        """Classify current setup; guidance only, never places orders."""
        if not self.settings.confirm_enabled:
            return "WAIT", "Подтверждение выключено — сначала проверь график.", None, None, None, None, None
        try:
            data = await self._get(BYBIT_KLINE_URL.format(symbol=symbol, interval=self.settings.confirmation_timeframe, limit=30))
            rows = list(reversed(data.get("result", {}).get("list", [])))
            closes = [float(r[4]) for r in rows if float(r[4]) > 0]
            n = self.settings.confirmation_candles
            if len(closes) < n + 5:
                return "WAIT", "Недостаточно свечей для подтверждения.", None, None, None, None, None
            # REST klines are reverse-sorted and the newest candle may still be open.
            recent = closes[-(n + 1):-1]
            recent_return = (recent[-1] / recent[0] - 1) * 100
            if direction == "PUMP":
                aligned = recent_return > 0 and (imbalance is None or imbalance >= 50)
                action = "LONG" if aligned else "WAIT"
                reason = "Импульс вверх сохраняется и стакан не против." if aligned else "После Pump нет достаточного подтверждения продолжения вверх — ждём."
            else:
                aligned = recent_return < 0 and (imbalance is None or imbalance < 50)
                action = "SHORT" if aligned else "WAIT"
                reason = "Импульс вниз сохраняется и стакан не против." if aligned else "После Dump нет достаточного подтверждения продолжения вниз — ждём."
            swing_low = min(closes[-8:])
            swing_high = max(closes[-8:])
            if action == "LONG":
                stop = swing_low
                risk = max(price - stop, price * 0.002)
                entry_low, entry_high = price * 0.998, price * 1.002
                tp1, tp2 = price + risk * self.settings.risk_reward_1, price + risk * self.settings.risk_reward_2
            elif action == "SHORT":
                stop = swing_high
                risk = max(stop - price, price * 0.002)
                entry_low, entry_high = price * 0.998, price * 1.002
                tp1, tp2 = price - risk * self.settings.risk_reward_1, price - risk * self.settings.risk_reward_2
            else:
                stop = tp1 = tp2 = entry_low = entry_high = None
            return action, reason, entry_low, entry_high, stop, tp1, tp2
        except Exception as exc:
            log.warning("Trade confirmation failed for %s: %s", symbol, type(exc).__name__)
            return "WAIT", "Не удалось получить подтверждение — сделку не открываем.", None, None, None, None, None

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
            if len(closes) > 15:
                closes = closes[:-1]
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
        lines.append(f"📡 Подтверждений: {s.confirmations}")
        lines.append(f"🧮 Качество сигнала: {s.quality_score}/100")
        lines.append("")
        lines.append(f"🧭 Решение: {s.trade_action}")
        lines.append(f"ℹ️ {s.trade_reason}")
        if s.trade_action in {"LONG", "SHORT"} and s.stop_price is not None:
            lines.append(f"🎯 Вход: {s.entry_low:.8g}–{s.entry_high:.8g}")
            lines.append(f"🛑 Stop: {s.stop_price:.8g}")
            lines.append(f"✅ TP1: {s.tp1:.8g}")
            lines.append(f"✅ TP2: {s.tp2:.8g}")
        else:
            lines.append("⏸ Вход не подтверждён — ждём, а не угадываем.")
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
