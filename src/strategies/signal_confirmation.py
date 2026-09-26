from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
BYBIT_ORDERBOOK_URL = "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=25"
BYBIT_OI_URL = "https://api.bybit.com/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=5min&limit=3"


@dataclass
class ConfirmationResult:
    symbol: str
    direction: str
    verdict: str
    score: int
    reasons: list[str]
    warnings: list[str]
    metrics: dict[str, float]


class SignalConfirmation:
    """Independent second-pass check. It never changes the primary signal."""

    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))

    async def stop(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None

    async def _get(self, url: str) -> dict:
        if not self.session:
            raise RuntimeError("SignalConfirmation is not started")
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if data.get("retCode", 0) != 0:
                raise RuntimeError(data.get("retMsg", "Bybit API error"))
            return data

    @staticmethod
    def _ema(values: list[float], period: int) -> float:
        k = 2.0 / (period + 1)
        ema = values[0]
        for value in values[1:]:
            ema = value * k + ema * (1.0 - k)
        return ema

    @staticmethod
    def _rsi(values: list[float], period: int = 14) -> float | None:
        if len(values) < period + 1:
            return None
        gains, losses = [], []
        for a, b in zip(values[-period - 1:-1], values[-period:]):
            d = b - a
            gains.append(max(d, 0.0))
            losses.append(max(-d, 0.0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    @staticmethod
    def _adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14):
        if len(closes) < period * 2 + 2:
            return None, None, None
        trs, plus_dm, minus_dm = [], [], []
        for i in range(1, len(closes)):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up if up > down and up > 0 else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)
            trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
        tr = sum(trs[:period])
        p = sum(plus_dm[:period])
        m = sum(minus_dm[:period])
        dx, pdis, mdis = [], [], []
        for i in range(period, len(trs)):
            tr = tr - tr / period + trs[i]
            p = p - p / period + plus_dm[i]
            m = m - m / period + minus_dm[i]
            pdi = 100.0 * p / tr if tr else 0.0
            mdi = 100.0 * m / tr if tr else 0.0
            denom = pdi + mdi
            dx.append(100.0 * abs(pdi - mdi) / denom if denom else 0.0)
            pdis.append(pdi)
            mdis.append(mdi)
        if len(dx) < period:
            return None, None, None
        adx = sum(dx[:period]) / period
        for value in dx[period:]:
            adx = (adx * (period - 1) + value) / period
        return adx, pdis[-1], mdis[-1]

    async def _klines(self, symbol: str, interval: str, limit: int = 80) -> list[dict]:
        data = await self._get(BYBIT_KLINE_URL.format(symbol=symbol, interval=interval, limit=limit))
        rows = list(reversed(data.get("result", {}).get("list", [])))
        if rows:
            rows = rows[:-1]  # closed candles only
        return [
            {"open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in rows if float(r[4]) > 0
        ]

    async def _orderbook(self, symbol: str) -> float | None:
        try:
            data = await self._get(BYBIT_ORDERBOOK_URL.format(symbol=symbol))
            bids = data.get("result", {}).get("b", [])
            asks = data.get("result", {}).get("a", [])
            bid = sum(float(p) * float(q) for p, q in bids)
            ask = sum(float(p) * float(q) for p, q in asks)
            return bid / (bid + ask) * 100.0 if bid + ask else None
        except Exception:
            return None

    async def _oi_change(self, symbol: str) -> float | None:
        try:
            data = await self._get(BYBIT_OI_URL.format(symbol=symbol))
            rows = data.get("result", {}).get("list", [])
            if len(rows) < 2:
                return None
            newest = float(rows[0]["openInterest"])
            oldest = float(rows[-1]["openInterest"])
            return (newest / oldest - 1.0) * 100.0 if oldest else None
        except Exception:
            return None

    async def check(self, symbol: str, direction: str) -> ConfirmationResult:
        try:
            one_m, five_m, book, oi = await asyncio.gather(
                self._klines(symbol, "1", 80),
                self._klines(symbol, "5", 80),
                self._orderbook(symbol),
                self._oi_change(symbol),
                return_exceptions=True,
            )
            if isinstance(one_m, Exception) or isinstance(five_m, Exception):
                raise RuntimeError("kline data unavailable")
            if len(one_m) < 30 or len(five_m) < 35:
                raise RuntimeError("not enough closed candles")

            closes1 = [x["close"] for x in one_m]
            closes5 = [x["close"] for x in five_m]
            highs5 = [x["high"] for x in five_m]
            lows5 = [x["low"] for x in five_m]
            ema9 = self._ema(closes1[-40:], 9)
            ema21 = self._ema(closes1[-40:], 21)
            ema9_prev = self._ema(closes1[-41:-1], 9)
            rsi = self._rsi(closes1, 14)
            adx, pdi, mdi = self._adx(highs5, lows5, closes5, 14)
            current_volume = one_m[-1]["volume"]
            baseline = one_m[-21:-1]
            avg_volume = sum(x["volume"] for x in baseline) / len(baseline)
            volume_ratio = current_volume / avg_volume if avg_volume else 0.0

            bullish = direction == "PUMP"
            score = 0
            reasons, warnings = [], []

            ema_aligned = ema9 > ema21 if bullish else ema9 < ema21
            ema_slope = ema9 > ema9_prev if bullish else ema9 < ema9_prev
            if ema_aligned:
                score += 20
                reasons.append("EMA 9/21 подтверждают направление")
            else:
                warnings.append("EMA 9/21 пока не подтверждают направление")
            if ema_slope:
                score += 10
                reasons.append("EMA 9 имеет нужный наклон")

            if rsi is not None:
                momentum_ok = rsi >= 55 if bullish else rsi <= 45
                if momentum_ok:
                    score += 15
                    reasons.append(f"RSI 1m подтверждает momentum ({rsi:.1f})")
                else:
                    warnings.append(f"RSI 1m не подтверждает momentum ({rsi:.1f})")

            if adx is not None and pdi is not None and mdi is not None:
                trend_ok = adx >= 18 and (pdi > mdi if bullish else mdi > pdi)
                if trend_ok:
                    score += 20 if adx >= 25 else 12
                    reasons.append(f"ADX 5m={adx:.1f}, направление тренда совпадает")
                else:
                    warnings.append(f"ADX/DI 5m не подтверждают направление (ADX {adx:.1f})")

            if volume_ratio >= 1.30:
                score += 20
                reasons.append(f"Объём 1m выше среднего в {volume_ratio:.1f}x")
            elif volume_ratio >= 1.10:
                score += 10
                reasons.append(f"Объём 1m повышен до {volume_ratio:.1f}x")
            else:
                warnings.append(f"Нет заметного всплеска объёма ({volume_ratio:.1f}x)")

            if isinstance(book, (int, float)):
                book_ok = book >= 52 if bullish else book <= 48
                book_strong = book >= 57 if bullish else book <= 43
                if book_ok:
                    score += 15 if book_strong else 10
                    reasons.append(f"Стакан поддерживает направление ({book:.1f}% bid)")
                else:
                    warnings.append(f"Стакан не поддерживает направление ({book:.1f}% bid)")

            if isinstance(oi, (int, float)):
                if oi >= 0.5:
                    score += 10
                    reasons.append(f"Open Interest растёт ({oi:+.2f}% за доступное окно)")
                else:
                    warnings.append(f"OI не растёт ({oi:+.2f}%)")

            positives = len(reasons)
            if score >= 65 and positives >= 3:
                verdict = "ПОДТВЕРЖДЕНО"
            elif score >= 50 and positives >= 2:
                verdict = "ОСТОРОЖНО / ЖДАТЬ"
            else:
                verdict = "НЕ ПОДТВЕРЖДЕНО"

            return ConfirmationResult(
                symbol=symbol, direction=direction, verdict=verdict,
                score=min(score, 100), reasons=reasons, warnings=warnings,
                metrics={
                    "rsi_1m": rsi if rsi is not None else -1.0,
                    "adx_5m": adx if adx is not None else -1.0,
                    "volume_ratio": volume_ratio,
                    "orderbook_bid_pct": float(book) if isinstance(book, (int, float)) else -1.0,
                    "oi_change_pct": float(oi) if isinstance(oi, (int, float)) else -999.0,
                },
            )
        except Exception as exc:
            log.warning("Independent confirmation failed for %s: %s", symbol, type(exc).__name__)
            return ConfirmationResult(
                symbol=symbol, direction=direction, verdict="ПРОВЕРКА НЕ ПОЛУЧЕНА",
                score=0, reasons=[],
                warnings=["Свежие подтверждающие данные не получены; основной сигнал не изменён."],
                metrics={},
            )

    @staticmethod
    def format_result(result: ConfirmationResult) -> str:
        icon = "🟢" if result.direction == "PUMP" else "🔴"
        action = "LONG" if result.direction == "PUMP" else "SHORT"
        lines = [
            f"{icon} 🔎 ВТОРАЯ ПРОВЕРКА · {result.symbol.removesuffix('USDT')}",
            f"Исходный импульс: {'PUMP' if result.direction == 'PUMP' else 'DUMP'}",
            f"Решение: {result.verdict}",
            f"Подтверждение: {result.score}/100",
        ]
        if result.reasons:
            lines += ["", "✅ Что подтверждает:"] + [f"• {x}" for x in result.reasons[:5]]
        if result.warnings:
            lines += ["", "⚠️ Что мешает:"] + [f"• {x}" for x in result.warnings[:4]]
        m = result.metrics
        if m:
            lines += [
                "",
                f"RSI 1m: {m.get('rsi_1m', -1):.1f} · ADX 5m: {m.get('adx_5m', -1):.1f} · Volume: {m.get('volume_ratio', 0):.1f}x",
            ]
            if m.get("orderbook_bid_pct", -1) >= 0:
                lines.append(f"Стакан: {m['orderbook_bid_pct']:.1f}% bid")
            if m.get("oi_change_pct", -999) > -999:
                lines.append(f"OI: {m['oi_change_pct']:+.2f}%")
        lines += [""]
        if result.verdict == "ПОДТВЕРЖДЕНО":
            lines.append(f"➡️ Быстрая проверка подтверждает попытку входа в {action}. Это не гарантия движения.")
        elif result.verdict == "ОСТОРОЖНО / ЖДАТЬ":
            lines.append("➡️ Импульс есть, но подтверждений недостаточно. Лучше не догонять движение.")
        elif result.verdict == "НЕ ПОДТВЕРЖДЕНО":
            lines.append("➡️ Исходный сигнал остаётся в чате, но дополнительная проверка вход не подтверждает.")
        else:
            lines.append("➡️ Дополнительная проверка не получена. Ничего не выдумываем.")
        return "\n".join(lines)
