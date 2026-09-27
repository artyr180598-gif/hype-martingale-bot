from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
BYBIT_TRADES_URL = "https://api.bybit.com/v5/market/recent-trade?category=linear&symbol={symbol}&limit=100"
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
    """Fast, independent impulse check.

    This layer does not replace the primary Pump/Dump scanner and never invents
    a direction. It checks a candidate using only fresh market data:
    price structure, aggressive trade flow/volume, OI participation and
    short-lived orderbook persistence.
    """

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

    async def _klines(self, symbol: str, interval: str, limit: int = 60) -> list[dict]:
        data = await self._get(
            BYBIT_KLINE_URL.format(symbol=symbol, interval=interval, limit=limit)
        )
        rows = list(reversed(data.get("result", {}).get("list", [])))
        if rows:
            rows = rows[:-1]  # completed candles only
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

    async def _trades(self, symbol: str) -> tuple[float, float, float, int] | None:
        """Return buy quote volume, sell quote volume, delta %, trade count."""
        try:
            data = await self._get(BYBIT_TRADES_URL.format(symbol=symbol))
            rows = data.get("result", {}).get("list", [])
            buy = 0.0
            sell = 0.0
            count = 0
            for row in rows:
                price = float(row.get("price", 0))
                size = float(row.get("size", 0))
                if price <= 0 or size <= 0:
                    continue
                value = price * size
                side = str(row.get("side", "")).lower()
                if side == "buy":
                    buy += value
                    count += 1
                elif side == "sell":
                    sell += value
                    count += 1
            total = buy + sell
            if total <= 0:
                return None
            return buy, sell, (buy - sell) / total * 100.0, count
        except Exception:
            return None

    async def _orderbook_once(self, symbol: str) -> float | None:
        try:
            data = await self._get(BYBIT_ORDERBOOK_URL.format(symbol=symbol))
            bids = data.get("result", {}).get("b", [])
            asks = data.get("result", {}).get("a", [])
            # Use notional depth so a large amount at a higher price is weighted
            # by its actual quote value. This is a snapshot, not a prediction.
            bid = sum(float(p) * float(q) for p, q in bids)
            ask = sum(float(p) * float(q) for p, q in asks)
            return bid / (bid + ask) * 100.0 if bid + ask else None
        except Exception:
            return None

    async def _orderbook_persistence(self, symbol: str) -> tuple[float | None, float | None]:
        first = await self._orderbook_once(symbol)
        await asyncio.sleep(0.6)
        second = await self._orderbook_once(symbol)
        if first is None and second is None:
            return None, None
        values = [x for x in (first, second) if x is not None]
        return sum(values) / len(values), max(values) - min(values) if len(values) > 1 else 0.0

    async def _oi_change(self, symbol: str) -> float | None:
        try:
            data = await self._get(BYBIT_OI_URL.format(symbol=symbol))
            rows = data.get("result", {}).get("list", [])
            if len(rows) < 2:
                return None
            newest_row = rows[0]
            oldest_row = rows[-1]
            # Bybit changed OI methodology in June 2026. Prefer the current
            # single-sided field when available; fall back only for compatibility.
            newest = float(
                newest_row.get("singleOpenInterest")
                or newest_row.get("openInterest")
                or 0
            )
            oldest = float(
                oldest_row.get("singleOpenInterest")
                or oldest_row.get("openInterest")
                or 0
            )
            return (newest / oldest - 1.0) * 100.0 if oldest else None
        except Exception:
            return None

    async def check(self, symbol: str, direction: str) -> ConfirmationResult:
        try:
            one_m, five_m, trades, oi = await asyncio.gather(
                self._klines(symbol, "1", 70),
                self._klines(symbol, "5", 70),
                self._trades(symbol),
                self._oi_change(symbol),
                return_exceptions=True,
            )
            if isinstance(one_m, Exception) or isinstance(five_m, Exception):
                raise RuntimeError("kline data unavailable")
            if len(one_m) < 30 or len(five_m) < 25:
                raise RuntimeError("not enough closed candles")

            bullish = direction == "PUMP"
            score = 0
            reasons: list[str] = []
            warnings: list[str] = []

            # 35 points: price structure is the primary confirmation.
            window = five_m[-13:-1]
            latest = five_m[-1]
            previous = five_m[-2]
            local_high = max(x["high"] for x in window)
            local_low = min(x["low"] for x in window)
            if bullish:
                breakout = latest["close"] >= local_high * 0.999
                continuation = latest["high"] > previous["high"] and latest["low"] >= previous["low"]
                body = (latest["close"] - latest["open"]) / latest["open"] * 100.0
                if breakout:
                    score += 25
                    reasons.append("5m цена удерживает локальный breakout")
                elif continuation:
                    score += 18
                    reasons.append("5m структура продолжает HH/HL")
                else:
                    warnings.append("5m структура не подтверждает продолжение вверх")
                if body > 0.15:
                    score += 10
                    reasons.append(f"Последняя закрытая 5m свеча направленная (+{body:.2f}%)")
                elif body < -0.15:
                    warnings.append(f"Последняя 5m свеча закрылась против импульса ({body:+.2f}%)")
            else:
                breakdown = latest["close"] <= local_low * 1.001
                continuation = latest["low"] < previous["low"] and latest["high"] <= previous["high"]
                body = (latest["close"] - latest["open"]) / latest["open"] * 100.0
                if breakdown:
                    score += 25
                    reasons.append("5m цена удерживает локальный breakdown")
                elif continuation:
                    score += 18
                    reasons.append("5m структура продолжает LL/LH")
                else:
                    warnings.append("5m структура не подтверждает продолжение вниз")
                if body < -0.15:
                    score += 10
                    reasons.append(f"Последняя закрытая 5m свеча направленная ({body:.2f}%)")
                elif body > 0.15:
                    warnings.append(f"Последняя 5m свеча закрылась против импульса ({body:+.2f}%)")

            # 30 points: real executed trade flow + relative 1m volume.
            closes = [x["close"] for x in one_m]
            volumes = [x["volume"] for x in one_m]
            avg_volume = sum(volumes[-21:-1]) / 20.0
            volume_ratio = volumes[-1] / avg_volume if avg_volume else 1.0
            flow_delta = None
            buy_value = sell_value = 0.0
            trade_count = 0
            if not isinstance(trades, Exception) and trades is not None:
                buy_value, sell_value, flow_delta, trade_count = trades
                flow_ok = flow_delta > 8.0 if bullish else flow_delta < -8.0
                if flow_ok:
                    score += 20
                    side = "покупателей" if bullish else "продавцов"
                    reasons.append(f"Поток сделок в сторону импульса: delta {flow_delta:+.1f}% ({side})")
                elif (bullish and flow_delta < -8.0) or (not bullish and flow_delta > 8.0):
                    warnings.append(f"Поток сделок идёт против направления: delta {flow_delta:+.1f}%")
                else:
                    score += 8
                    reasons.append(f"Поток сделок без сильного перекоса: delta {flow_delta:+.1f}%")
            else:
                warnings.append("Свежий поток сделок не получен")

            if volume_ratio >= 1.35:
                score += 10
                reasons.append(f"1m объём расширен до {volume_ratio:.1f}x среднего")
            elif volume_ratio >= 0.95:
                score += 5
                reasons.append(f"1m объём нормальный ({volume_ratio:.1f}x)")
            else:
                warnings.append(f"1m объём слабый ({volume_ratio:.1f}x)")

            # 20 points: OI is participation confirmation, never a direction oracle.
            oi_value = float(oi) if isinstance(oi, (int, float)) else None
            if oi_value is not None:
                if oi_value >= 0.8:
                    score += 20
                    reasons.append(f"OI растёт вместе с движением ({oi_value:+.2f}%)")
                elif oi_value >= 0.2:
                    score += 10
                    reasons.append(f"OI слегка растёт ({oi_value:+.2f}%)")
                elif oi_value <= -1.0:
                    warnings.append(f"OI снижается ({oi_value:+.2f}%) — участие ослабевает")
                else:
                    warnings.append(f"OI почти не меняется ({oi_value:+.2f}%)")
            else:
                warnings.append("OI не получен")

            # 15 points: orderbook must persist, not merely flash once.
            book_avg, book_spread = await self._orderbook_persistence(symbol)
            book_value = float(book_avg) if book_avg is not None else None
            if book_value is not None:
                if bullish:
                    book_ok = book_value >= 52.0
                else:
                    book_ok = book_value <= 48.0
                stable = book_spread <= 4.0
                if book_ok and stable:
                    score += 15
                    reasons.append(f"Стакан поддерживает направление и стабилен ({book_value:.1f}% bid)")
                elif book_ok:
                    score += 7
                    warnings.append(f"Стакан поддерживает направление, но быстро меняется ({book_spread:.1f} п.п.)")
                else:
                    warnings.append(f"Стакан не поддерживает направление ({book_value:.1f}% bid)")
            else:
                warnings.append("Стакан не получен")

            # Score is confluence strength, NOT probability.
            core_ok = (
                score >= 60
                and (
                    ("Поток сделок в сторону импульса" in " ".join(reasons))
                    or (book_value is not None and ((bullish and book_value >= 53.0) or (not bullish and book_value <= 47.0)))
                )
                and not any("против направления" in w or "закрылась против" in w for w in warnings)
            )
            if core_ok and score >= 78:
                verdict = "СИЛЬНЫЙ ИМПУЛЬС"
            elif core_ok:
                verdict = "ИМПУЛЬС ПОДТВЕРЖДЁН"
            elif score >= 48:
                verdict = "ИМПУЛЬС ЕСТЬ, НО СЛАБЕЕТ"
            else:
                verdict = "НЕ ПОДТВЕРЖДЕНО"

            return ConfirmationResult(
                symbol=symbol,
                direction=direction,
                verdict=verdict,
                score=min(score, 100),
                reasons=reasons,
                warnings=warnings,
                metrics={
                    "volume_ratio": volume_ratio,
                    "trade_delta_pct": flow_delta if flow_delta is not None else -999.0,
                    "trade_count": float(trade_count),
                    "buy_value": buy_value,
                    "sell_value": sell_value,
                    "oi_change_pct": oi_value if oi_value is not None else -999.0,
                    "orderbook_bid_pct": book_value if book_value is not None else -1.0,
                    "orderbook_spread_pp": book_spread if book_spread is not None else -1.0,
                    "last_5m_body_pct": body,
                    "price_5m": closes[-1] if closes else -1.0,
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
    def format_result(result: ConfirmationResult, label: str = "БЫСТРАЯ ПРОВЕРКА") -> str:
        icon = "🟢" if result.direction == "PUMP" else "🔴"
        lines = [
            f"{icon} ⚡ {label} · {result.symbol.removesuffix('USDT')}",
            f"Импульс: {'PUMP → LONG' if result.direction == 'PUMP' else 'DUMP → SHORT'}",
            f"Сила подтверждения: {result.score}/100",
            f"Статус: {result.verdict}",
        ]
        if result.reasons:
            lines += ["", "✅ Поддерживает:"] + [f"• {x}" for x in result.reasons[:5]]
        if result.warnings:
            lines += ["", "⚠️ Слабые места:"] + [f"• {x}" for x in result.warnings[:4]]
        m = result.metrics
        if m:
            lines += [
                "",
                f"Flow delta: {m.get('trade_delta_pct', -999):+.1f}% · Volume: {m.get('volume_ratio', 0):.1f}x · "
                f"OI: {m.get('oi_change_pct', -999):+.2f}%",
            ]
            if m.get("orderbook_bid_pct", -1) >= 0:
                lines.append(
                    f"Стакан: {m['orderbook_bid_pct']:.1f}% bid · разброс {m.get('orderbook_spread_pp', 0):.1f} п.п."
                )
        if result.verdict in {"СИЛЬНЫЙ ИМПУЛЬС", "ИМПУЛЬС ПОДТВЕРЖДЁН"}:
            lines.append("➡️ Быстрая проверка поддерживает основной сигнал. Это не гарантия движения.")
        elif result.verdict == "ИМПУЛЬС ЕСТЬ, НО СЛАБЕЕТ":
            lines.append("➡️ Основной сигнал найден, но свежего подтверждения недостаточно.")
        elif result.verdict == "НЕ ПОДТВЕРЖДЕНО":
            lines.append("➡️ Продолжение сейчас не подтверждено. Ничего не выдумываем.")
        else:
            lines.append("➡️ Дополнительные данные не получены. Основной сигнал не изменён.")
        return "\n".join(lines)
