from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
BYBIT_ORDERBOOK_URL = "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=25"
BYBIT_OI_URL = "https://api.bybit.com/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=5min&limit=4"


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
    """Independent continuation check. It never changes or blocks the primary signal."""

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
    def _adx_series(
        highs: list[float], lows: list[float], closes: list[float], period: int = 14
    ) -> tuple[float, float, float, float, float] | None:
        """Return ADX, previous ADX, +DI and -DI from completed candles."""
        if len(closes) < period * 2 + 4:
            return None
        trs, plus_dm, minus_dm = [], [], []
        for i in range(1, len(closes)):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up if up > down and up > 0 else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)
            trs.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )

        tr = sum(trs[:period])
        p = sum(plus_dm[:period])
        m = sum(minus_dm[:period])
        dx: list[float] = []
        pdis: list[float] = []
        mdis: list[float] = []

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

        if len(dx) < period + 1:
            return None

        adx_values: list[float] = [sum(dx[:period]) / period]
        for value in dx[period:]:
            adx_values.append((adx_values[-1] * (period - 1) + value) / period)
        if len(adx_values) < 2:
            return None
        return adx_values[-1], adx_values[-2], pdis[-1], mdis[-1], pdis[-2] - mdis[-2]

    @staticmethod
    def _atr_stats(
        highs: list[float], lows: list[float], closes: list[float], period: int = 14
    ) -> tuple[float, float] | None:
        if len(closes) < period * 2 + 2:
            return None
        trs = []
        for i in range(1, len(closes)):
            trs.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )
        atr_values: list[float] = []
        atr = sum(trs[:period]) / period
        atr_values.append(atr)
        for value in trs[period:]:
            atr = (atr * (period - 1) + value) / period
            atr_values.append(atr)
        if len(atr_values) < 2:
            return None
        current = atr_values[-1]
        baseline_values = atr_values[-21:-1] if len(atr_values) >= 21 else atr_values[:-1]
        baseline = sum(baseline_values) / len(baseline_values) if baseline_values else current
        return current, current / baseline if baseline else 1.0

    async def _klines(self, symbol: str, interval: str, limit: int = 100) -> list[dict]:
        data = await self._get(BYBIT_KLINE_URL.format(symbol=symbol, interval=interval, limit=limit))
        rows = list(reversed(data.get("result", {}).get("list", [])))
        if rows:
            rows = rows[:-1]  # closed candles only
        return [
            {
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
            if float(r[4]) > 0
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
                self._klines(symbol, "1", 100),
                self._klines(symbol, "5", 100),
                self._orderbook(symbol),
                self._oi_change(symbol),
                return_exceptions=True,
            )
            if isinstance(one_m, Exception) or isinstance(five_m, Exception):
                raise RuntimeError("kline data unavailable")
            if len(one_m) < 35 or len(five_m) < 45:
                raise RuntimeError("not enough closed candles")

            closes1 = [x["close"] for x in one_m]
            closes5 = [x["close"] for x in five_m]
            highs5 = [x["high"] for x in five_m]
            lows5 = [x["low"] for x in five_m]

            ema9 = self._ema(closes1[-50:], 9)
            ema21 = self._ema(closes1[-50:], 21)
            ema9_prev = self._ema(closes1[-51:-1], 9)
            rsi = self._rsi(closes1, 14)
            adx_data = self._adx_series(highs5, lows5, closes5, 14)
            atr_data = self._atr_stats(highs5, lows5, closes5, 14)

            current_volume = one_m[-1]["volume"]
            baseline = one_m[-21:-1]
            avg_volume = sum(x["volume"] for x in baseline) / len(baseline)
            volume_ratio = current_volume / avg_volume if avg_volume else 1.0

            bullish = direction == "PUMP"
            score = 0
            reasons: list[str] = []
            warnings: list[str] = []

            # 25 points: actual price structure, deliberately the heaviest component.
            structure_window = five_m[-21:-1]
            structure_high = max(x["high"] for x in structure_window)
            structure_low = min(x["low"] for x in structure_window)
            latest5 = five_m[-1]
            prior5 = five_m[-2]
            if bullish:
                breakout = latest5["close"] >= structure_high * 0.999
                higher_structure = latest5["high"] > prior5["high"] and latest5["low"] >= prior5["low"]
                structure_ok = breakout or higher_structure
                if breakout:
                    score += 20
                    reasons.append("Цена удерживается у/выше локального 5m breakout-уровня")
                elif higher_structure:
                    score += 15
                    reasons.append("5m структура делает higher high / higher low")
                else:
                    warnings.append("5m структура ещё не подтверждает продолжение вверх")
            else:
                breakdown = latest5["close"] <= structure_low * 1.001
                lower_structure = latest5["low"] < prior5["low"] and latest5["high"] <= prior5["high"]
                structure_ok = breakdown or lower_structure
                if breakdown:
                    score += 20
                    reasons.append("Цена удерживается у/ниже локального 5m breakdown-уровня")
                elif lower_structure:
                    score += 15
                    reasons.append("5m структура делает lower low / lower high")
                else:
                    warnings.append("5m структура ещё не подтверждает продолжение вниз")
            if structure_ok and abs(latest5["close"] - prior5["close"]) / prior5["close"] > 0.002:
                score += 5
                reasons.append("Последняя закрытая 5m свеча сохраняет направленный импульс")

            # 20 points: ADX + DI direction + ADX slope.
            if adx_data is not None:
                adx, adx_prev, pdi, mdi, previous_di_spread = adx_data
                directional_ok = pdi > mdi if bullish else mdi > pdi
                adx_rising = adx > adx_prev
                if directional_ok:
                    score += 10
                    reasons.append(f"DMI 5m направлен в сторону импульса (+DI {pdi:.1f} / -DI {mdi:.1f})")
                else:
                    warnings.append(f"DMI 5m не подтверждает направление (+DI {pdi:.1f} / -DI {mdi:.1f})")
                if adx_rising:
                    score += 7
                    reasons.append(f"ADX растёт {adx_prev:.1f}→{adx:.1f}")
                elif adx >= 25:
                    score += 3
                    reasons.append(f"ADX остаётся сильным ({adx:.1f}), но уже не ускоряется")
                else:
                    warnings.append(f"ADX слабый/плоский ({adx:.1f})")
                if adx >= 18:
                    score += 3
            else:
                warnings.append("ADX/DMI не получены")

            # 15 points: EMA direction and slope, without making it a hard gate.
            ema_aligned = ema9 > ema21 if bullish else ema9 < ema21
            ema_slope = ema9 > ema9_prev if bullish else ema9 < ema9_prev
            if ema_aligned:
                score += 10
                reasons.append("EMA 9/21 согласованы с направлением")
            else:
                warnings.append("EMA 9/21 пока не согласованы с направлением")
            if ema_slope:
                score += 5
                reasons.append("EMA 9 продолжает двигаться в сторону импульса")

            # 15 points: volume is graded, never mandatory.
            if volume_ratio >= 1.50:
                score += 15
                reasons.append(f"Объём 1m расширился до {volume_ratio:.1f}x среднего")
            elif volume_ratio >= 1.15:
                score += 9
                reasons.append(f"Объём 1m выше среднего ({volume_ratio:.1f}x)")
            elif volume_ratio >= 0.90:
                score += 4
                warnings.append(f"Объём близок к среднему ({volume_ratio:.1f}x)")
            else:
                warnings.append(f"Объём ниже среднего ({volume_ratio:.1f}x)")

            # 10 points: open interest is participation confirmation, not a direction oracle.
            if isinstance(oi, (int, float)):
                if oi >= 1.0:
                    score += 10
                    reasons.append(f"Open Interest растёт ({oi:+.2f}% за доступное окно)")
                elif oi >= 0.25:
                    score += 5
                    reasons.append(f"Open Interest слегка растёт ({oi:+.2f}%)")
                elif oi <= -1.0:
                    warnings.append(f"Open Interest резко снижается ({oi:+.2f}%)")
                else:
                    warnings.append(f"OI без заметного роста ({oi:+.2f}%)")

            # 5 points: ATR expansion catches moves that are actually broadening.
            atr_ratio = 1.0
            if atr_data is not None:
                _, atr_ratio = atr_data
                if atr_ratio >= 1.15:
                    score += 5
                    reasons.append(f"ATR 5m расширяется ({atr_ratio:.2f}x базового)")
                elif atr_ratio < 0.85:
                    warnings.append(f"ATR 5m сжимается ({atr_ratio:.2f}x базового)")

            # 5 points: orderbook is deliberately weak because a snapshot can change quickly.
            book_value = float(book) if isinstance(book, (int, float)) else None
            if book_value is not None:
                book_ok = book_value >= 51.5 if bullish else book_value <= 48.5
                if book_ok:
                    score += 5
                    reasons.append(f"Текущий стакан слегка поддерживает направление ({book_value:.1f}% bid)")
                else:
                    warnings.append(f"Стакан сейчас не поддерживает направление ({book_value:.1f}% bid)")

            # RSI is informational only. Extreme RSI is not automatically bullish/bearish.
            if rsi is not None:
                if bullish and 50 <= rsi <= 78:
                    reasons.append(f"RSI 1m в рабочей зоне momentum ({rsi:.1f})")
                elif not bullish and 22 <= rsi <= 50:
                    reasons.append(f"RSI 1m в рабочей зоне momentum ({rsi:.1f})")
                elif (bullish and rsi > 85) or (not bullish and rsi < 15):
                    warnings.append(f"RSI 1m экстремальный ({rsi:.1f}) — возможен перегрев")

            # Keep the layer permissive: it describes confidence, it never blocks the primary alert.
            positives = len(reasons)
            if score >= 72 and positives >= 4:
                verdict = "СИЛЬНОЕ ПРОДОЛЖЕНИЕ"
            elif score >= 58 and positives >= 3:
                verdict = "ПРОДОЛЖЕНИЕ ВЕРОЯТНО"
            elif score >= 42 and positives >= 2:
                verdict = "СМЕШАННО / ЖДАТЬ"
            else:
                verdict = "ПРОДОЛЖЕНИЕ НЕ ПОДТВЕРЖДЕНО"

            return ConfirmationResult(
                symbol=symbol,
                direction=direction,
                verdict=verdict,
                score=min(score, 100),
                reasons=reasons,
                warnings=warnings,
                metrics={
                    "rsi_1m": rsi if rsi is not None else -1.0,
                    "adx_5m": adx_data[0] if adx_data else -1.0,
                    "adx_prev_5m": adx_data[1] if adx_data else -1.0,
                    "plus_di_5m": adx_data[2] if adx_data else -1.0,
                    "minus_di_5m": adx_data[3] if adx_data else -1.0,
                    "volume_ratio": volume_ratio,
                    "atr_ratio": atr_ratio,
                    "orderbook_bid_pct": book_value if book_value is not None else -1.0,
                    "oi_change_pct": float(oi) if isinstance(oi, (int, float)) else -999.0,
                },
            )
        except Exception as exc:
            log.warning("Independent confirmation failed for %s: %s", symbol, type(exc).__name__)
            return ConfirmationResult(
                symbol=symbol,
                direction=direction,
                verdict="ПРОВЕРКА НЕ ПОЛУЧЕНА",
                score=0,
                reasons=[],
                warnings=["Свежие подтверждающие данные не получены; основной сигнал не изменён."],
                metrics={},
            )

    @staticmethod
    def format_result(result: ConfirmationResult, label: str = "ВТОРАЯ ПРОВЕРКА") -> str:
        icon = "🟢" if result.direction == "PUMP" else "🔴"
        action = "LONG" if result.direction == "PUMP" else "SHORT"
        lines = [
            f"{icon} 🔎 {label} · {result.symbol.removesuffix('USDT')}",
            f"Исходный импульс: {'PUMP' if result.direction == 'PUMP' else 'DUMP'}",
            f"Решение: {result.verdict}",
            f"Подтверждение: {result.score}/100",
        ]
        if result.reasons:
            lines += ["", "✅ Что поддерживает продолжение:"] + [f"• {x}" for x in result.reasons[:6]]
        if result.warnings:
            lines += ["", "⚠️ Что ослабляет картину:"] + [f"• {x}" for x in result.warnings[:5]]
        m = result.metrics
        if m:
            lines += [
                "",
                f"ADX 5m: {m.get('adx_5m', -1):.1f} → {m.get('adx_prev_5m', -1):.1f} · "
                f"DI: +{m.get('plus_di_5m', -1):.1f}/-{m.get('minus_di_5m', -1):.1f}",
                f"Volume 1m: {m.get('volume_ratio', 0):.1f}x · ATR 5m: {m.get('atr_ratio', 1):.2f}x · "
                f"RSI 1m: {m.get('rsi_1m', -1):.1f}",
            ]
            if m.get("orderbook_bid_pct", -1) >= 0:
                lines.append(f"Стакан: {m['orderbook_bid_pct']:.1f}% bid")
            if m.get("oi_change_pct", -999) > -999:
                lines.append(f"OI: {m['oi_change_pct']:+.2f}%")

        if result.verdict == "СИЛЬНОЕ ПРОДОЛЖЕНИЕ":
            lines.append(f"➡️ Дополнительная модель видит сильную структуру продолжения {action}. Это не гарантия движения.")
        elif result.verdict == "ПРОДОЛЖЕНИЕ ВЕРОЯТНО":
            lines.append(f"➡️ Большинство проверок поддерживает продолжение {action}, но это не гарантия.")
        elif result.verdict == "СМЕШАННО / ЖДАТЬ":
            lines.append("➡️ Импульс есть, но подтверждения смешанные. Основной сигнал не отменён.")
        elif result.verdict == "ПРОДОЛЖЕНИЕ НЕ ПОДТВЕРЖДЕНО":
            lines.append("➡️ Основной сигнал остаётся в силе как событие, но продолжение сейчас не подтверждено.")
        else:
            lines.append("➡️ Дополнительная проверка не получена. Ничего не выдумываем.")
        return "\n".join(lines)
