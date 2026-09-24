"""Signal-only multi-factor market analysis built on HyperDataHub."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from statistics import mean

log = logging.getLogger(__name__)


@dataclass
class AnalysisSignal:
    symbol: str
    direction: str
    score: int
    entry_low: float
    entry_high: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    reasons: list[str]
    warnings: list[str]
    data: dict

    @property
    def valid(self) -> bool:
        return self.direction in {"LONG", "SHORT"} and self.score >= 70


class ConfluenceAnalyzer:
    """Transparent confluence scanner over HyperData's existing data feeds."""

    def __init__(self, hub, min_score: int = 70) -> None:
        self.hub = hub
        self.min_score = min_score

    async def analyze(self, symbol: str) -> AnalysisSignal | None:
        symbol = symbol.upper()
        asset = self.hub.market.assets.get(symbol)
        if not asset or asset.price <= 0:
            return None

        reasons: list[str] = []
        warnings: list[str] = []
        long_points = 0
        short_points = 0
        evidence = {}

        candles = await self.hub.market.get_candles(symbol, "15m", 120)
        if len(candles) < 30:
            warnings.append("15m candles incomplete")
        else:
            closes = [float(c["close"]) for c in candles]
            highs = [float(c["high"]) for c in candles]
            lows = [float(c["low"]) for c in candles]
            price = closes[-1]
            fast = mean(closes[-8:])
            slow = mean(closes[-32:])
            recent_high = max(highs[-24:])
            recent_low = min(lows[-24:])
            evidence["trend"] = {"fast_mean": fast, "slow_mean": slow}
            if fast > slow and price > slow:
                long_points += 20
                reasons.append("15m structure is above its recent mean")
            elif fast < slow and price < slow:
                short_points += 20
                reasons.append("15m structure is below its recent mean")
            else:
                warnings.append("15m trend is mixed")

            h1 = await self.hub.market.get_candles(symbol, "1h", 80)
            if len(h1) >= 25:
                hcl = [float(c["close"]) for c in h1]
                hfast, hslow = mean(hcl[-6:]), mean(hcl[-20:])
                if hfast > hslow:
                    long_points += 15
                    reasons.append("1h regime confirms upside")
                elif hfast < hslow:
                    short_points += 15
                    reasons.append("1h regime confirms downside")
                else:
                    warnings.append("1h regime mixed")
            else:
                warnings.append("1h candles incomplete")

            evidence["range"] = {"recent_high": recent_high, "recent_low": recent_low}

        for tf, pts in (("5m", 15), ("15m", 10)):
            snap = self.hub.orderflow.get_snapshot(symbol, tf)
            if snap is None:
                warnings.append(f"{tf} order flow unavailable")
                continue
            evidence[f"cvd_{tf}"] = {"ofi": snap.ofi, "signal": snap.signal}
            if snap.ofi >= 0.15:
                long_points += pts
                reasons.append(f"{tf} order flow is buyer-dominant")
            elif snap.ofi <= -0.15:
                short_points += pts
                reasons.append(f"{tf} order flow is seller-dominant")
            else:
                warnings.append(f"{tf} order flow neutral")

        rates = self.hub.funding.get_all_for_symbol(symbol)
        if rates:
            avg_fr = mean(r.funding_rate_hourly for r in rates)
            evidence["funding_hourly_avg"] = avg_fr
            if avg_fr > 0.00015:
                short_points += 5
                reasons.append("funding is elevated, supporting short-side crowding context")
            elif avg_fr < -0.00015:
                long_points += 5
                reasons.append("funding is negative, supporting long-side crowding context")
        else:
            warnings.append("cross-exchange funding unavailable")

        ob = self.hub.get_orderbook(symbol)
        if ob:
            evidence["orderbook"] = {"imbalance": ob.imbalance, "spread": ob.spread}
            if ob.imbalance >= 0.15:
                long_points += 10
                reasons.append("orderbook imbalance favors bids")
            elif ob.imbalance <= -0.15:
                short_points += 10
                reasons.append("orderbook imbalance favors asks")
            else:
                warnings.append("orderbook imbalance neutral")
        else:
            warnings.append("orderbook unavailable")

        try:
            stats = self.hub.liquidations.get_stats(window_minutes=15)
            long_liq = float(stats.get("long_volume_usd", 0))
            short_liq = float(stats.get("short_volume_usd", 0))
            evidence["liquidations_15m"] = {"long_usd": long_liq, "short_usd": short_liq}
            if long_liq > short_liq * 1.5 and long_liq > 100_000:
                long_points += 5
                reasons.append("recent long liquidations provide downside-exhaustion context")
            elif short_liq > long_liq * 1.5 and short_liq > 100_000:
                short_points += 5
                reasons.append("recent short liquidations provide upside-exhaustion context")
        except Exception:
            warnings.append("liquidation statistics unavailable")

        total = max(long_points, short_points)
        direction = "LONG" if long_points > short_points else "SHORT" if short_points > long_points else "NONE"
        score = min(100, total)
        if direction == "NONE" or score < self.min_score:
            return None

        price = float(asset.price)
        atr = self._atr(candles[-30:]) if len(candles) >= 30 else price * 0.01
        atr = max(atr, price * 0.002)

        if direction == "LONG":
            entry_low, entry_high = price - atr * 0.20, price + atr * 0.05
            stop = price - atr * 1.25
            risk = price - stop
            tp1, tp2, tp3 = price + risk, price + risk * 1.8, price + risk * 2.6
        else:
            entry_low, entry_high = price - atr * 0.05, price + atr * 0.20
            stop = price + atr * 1.25
            risk = stop - price
            tp1, tp2, tp3 = price - risk, price - risk * 1.8, price - risk * 2.6

        evidence["atr_15m"] = atr
        evidence["price"] = price
        evidence["score_components"] = {"long": long_points, "short": short_points}

        return AnalysisSignal(
            symbol=symbol,
            direction=direction,
            score=score,
            entry_low=max(0.0, entry_low),
            entry_high=max(0.0, entry_high),
            stop=max(0.0, stop),
            tp1=max(0.0, tp1),
            tp2=max(0.0, tp2),
            tp3=max(0.0, tp3),
            reasons=reasons,
            warnings=warnings,
            data=evidence,
        )

    @staticmethod
    def _atr(candles: list[dict]) -> float:
        trs = []
        prev = None
        for c in candles:
            h, l = float(c["high"]), float(c["low"])
            tr = h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev))
            trs.append(tr)
            prev = float(c["close"])
        return mean(trs[-14:]) if trs else 0.0

    async def _analyze_one(self, symbol: str, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await asyncio.wait_for(self.analyze(symbol), timeout=20)
            except asyncio.TimeoutError:
                log.warning("Signal scan timeout for %s", symbol)
                return None
            except Exception:
                log.exception("Signal scan failed for %s", symbol)
                return None

    async def scan(self, symbols: list[str] | None = None, limit: int = 5) -> list[AnalysisSignal]:
        symbols = list(symbols or self.hub.symbols)
        if not symbols:
            log.warning("Signal scan has no symbols")
            return []

        # The old implementation analyzed every symbol sequentially. With 50
        # symbols and several HTTP requests per symbol, one slow exchange call
        # could make Telegram look frozen for minutes. Keep the same universe
        # and scoring, but analyze symbols concurrently with bounded pressure.
        concurrency = min(8, len(symbols))
        sem = asyncio.Semaphore(concurrency)
        tasks = [asyncio.create_task(self._analyze_one(symbol, sem)) for symbol in symbols]
        results = await asyncio.gather(*tasks)

        found = [signal for signal in results if signal and signal.valid]
        found.sort(key=lambda x: x.score, reverse=True)
        log.info(
            "Signal scan finished: symbols=%d valid=%d errors/timeouts=%d",
            len(symbols),
            len(found),
            len(symbols) - len(results),
        )
        return found[:limit]
