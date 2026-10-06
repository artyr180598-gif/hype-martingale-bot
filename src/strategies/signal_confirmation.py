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
    """Fast independent impulse check.

    The primary Pump/Dump scanner remains the trigger. This layer only asks
    whether the fresh market structure and flow support continuation.
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
        data = await self._get(BYBIT_KLINE_URL.format(symbol=symbol, interval=interval, limit=limit))
        rows = list(reversed(data.get("result", {}).get("list", [])))
        if rows:
            rows = rows[:-1]
        return [
            {"open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in rows if float(r[4]) > 0
        ]

    async def _trades(self, symbol: str) -> tuple[float, float, float, int] | None:
        try:
            data = await self._get(BYBIT_TRADES_URL.format(symbol=symbol))
            rows = data.get("result", {}).get("list", [])
            buy = sell = 0.0
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
            bid = sum(float(p) * float(q) for p, q in bids)
            ask = sum(float(p) * float(q) for p, q in asks)
            return bid / (bid + ask) * 100.0 if bid + ask else None
        except Exception:
            return None

    async def _orderbook_persistence(self, symbol: str) -> tuple[float | None, float | None]:
        # Four short snapshots are more informative than one snapshot while
        # keeping the confirmation fast. We measure persistence, not prediction.
        values: list[float] = []
        for i in range(4):
            value = await self._orderbook_once(symbol)
            if value is not None:
                values.append(value)
            if i < 3:
                await asyncio.sleep(0.25)
        if not values:
            return None, None
        return sum(values) / len(values), max(values) - min(values) if len(values) > 1 else 0.0

    async def _microstructure(self, symbol: str) -> dict:
        """Short REST microstructure sample for the candidate only.

        This is deliberately NOT called true OFI: true event-level OFI requires
        the WebSocket snapshot/delta stream. Here we measure repeated depth,
        micro-price, and a depth-change proxy without overstating the data.
        """
        snaps: list[dict] = []
        for i in range(4):
            try:
                data = await self._get(BYBIT_ORDERBOOK_URL.format(symbol=symbol))
                result = data.get("result", {})
                bids = [(float(p), float(q)) for p, q in result.get("b", []) if float(q) > 0]
                asks = [(float(p), float(q)) for p, q in result.get("a", []) if float(q) > 0]
                if bids and asks:
                    bid_value = sum(p * q for p, q in bids)
                    ask_value = sum(p * q for p, q in asks)
                    total = bid_value + ask_value
                    best_bid, best_ask = bids[0][0], asks[0][0]
                    mid = (best_bid + best_ask) / 2.0
                    micro = ((best_ask * bid_value) + (best_bid * ask_value)) / total
                    snaps.append({
                        "bid_pct": bid_value / total * 100.0,
                        "mid": mid,
                        "micro": micro,
                        "bids": bids,
                        "asks": asks,
                    })
            except Exception:
                pass
            if i < 3:
                await asyncio.sleep(0.25)

        if not snaps:
            return {}

        avg_bid = sum(x["bid_pct"] for x in snaps) / len(snaps)
        bid_range = max(x["bid_pct"] for x in snaps) - min(x["bid_pct"] for x in snaps)
        micro_offsets = [
            (x["micro"] - x["mid"]) / x["mid"] * 100.0
            for x in snaps if x["mid"]
        ]
        micro_offset = sum(micro_offsets) / len(micro_offsets) if micro_offsets else 0.0

        depth_flow_proxy = None
        if len(snaps) >= 2:
            first, last = snaps[0], snaps[-1]
            bid_change = sum(p * q for p, q in last["bids"]) - sum(p * q for p, q in first["bids"])
            ask_change = sum(p * q for p, q in last["asks"]) - sum(p * q for p, q in first["asks"])
            scale = abs(bid_change) + abs(ask_change)
            if scale > 0:
                depth_flow_proxy = (bid_change - ask_change) / scale * 100.0

        return {
            "bid_pct": avg_bid,
            "bid_range": bid_range,
            "micro_offset": micro_offset,
            "depth_flow_proxy": depth_flow_proxy if depth_flow_proxy is not None else 0.0,
            "snapshots": float(len(snaps)),
        }

    async def _oi_change(self, symbol: str) -> float | None:
        try:
            data = await self._get(BYBIT_OI_URL.format(symbol=symbol))
            rows = data.get("result", {}).get("list", [])
            if len(rows) < 2:
                return None
            newest_row, oldest_row = rows[0], rows[-1]
            newest = float(newest_row.get("singleOpenInterest") or newest_row.get("openInterest") or 0)
            oldest = float(oldest_row.get("singleOpenInterest") or oldest_row.get("openInterest") or 0)
            return (newest / oldest - 1.0) * 100.0 if oldest else None
        except Exception:
            return None

    async def check(self, symbol: str, direction: str) -> ConfirmationResult:
        try:
            one_m, five_m, fifteen_m, trades, oi = await asyncio.gather(
                self._klines(symbol, "1", 70),
                self._klines(symbol, "5", 70),
                self._klines(symbol, "15", 35),
                self._trades(symbol),
                self._oi_change(symbol),
                return_exceptions=True,
            )
            if any(isinstance(x, Exception) for x in (one_m, five_m, fifteen_m)):
                raise RuntimeError("kline data unavailable")
            if len(one_m) < 30 or len(five_m) < 25 or len(fifteen_m) < 15:
                raise RuntimeError("not enough closed candles")

            bullish = direction == "PUMP"
            score = 0
            reasons: list[str] = []
            warnings: list[str] = []

            # 35 points: immediate 5m structure.
            window = five_m[-13:-1]
            latest = five_m[-1]
            previous = five_m[-2]
            local_high = max(x["high"] for x in window)
            local_low = min(x["low"] for x in window)
            body = (latest["close"] - latest["open"]) / latest["open"] * 100.0
            if bullish:
                breakout = latest["close"] >= local_high * 0.999
                continuation = latest["high"] > previous["high"] and latest["low"] >= previous["low"]
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

            # 15m is context only. It can warn against a mature counter-trend move,
            # but it cannot veto a fresh early impulse by itself.
            context = fifteen_m[-1]
            context_body = (context["close"] - context["open"]) / context["open"] * 100.0
            context_aligned = context_body > 0 if bullish else context_body < 0
            if context_aligned:
                score += 5
                reasons.append(f"15m контекст совпадает с направлением ({context_body:+.2f}%)")
            elif abs(context_body) >= 0.35:
                warnings.append(f"15m контекст пока против направления ({context_body:+.2f}%)")

            # 30 points: executed trade flow + volume expansion.
            closes = [x["close"] for x in one_m]
            volumes = [x["volume"] for x in one_m]
            avg_volume = sum(volumes[-21:-1]) / 20.0
            volume_ratio = volumes[-1] / avg_volume if avg_volume else 1.0
            flow_delta = None
            buy_value = sell_value = 0.0
            trade_count = 0
            flow_aligned = False
            flow_against = False
            if not isinstance(trades, Exception) and trades is not None:
                buy_value, sell_value, flow_delta, trade_count = trades
                flow_aligned = flow_delta > 8.0 if bullish else flow_delta < -8.0
                flow_against = flow_delta < -8.0 if bullish else flow_delta > 8.0
                if flow_aligned:
                    score += 20
                    side = "покупателей" if bullish else "продавцов"
                    reasons.append(f"Поток сделок в сторону импульса: delta {flow_delta:+.1f}% ({side})")
                elif flow_against:
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

            # OI confirms participation, not direction.
            oi_value = float(oi) if isinstance(oi, (int, float)) else None
            oi_support = False
            oi_against = False
            if oi_value is not None:
                if oi_value >= 0.8:
                    score += 20
                    oi_support = True
                    reasons.append(f"OI растёт вместе с движением ({oi_value:+.2f}%)")
                elif oi_value >= 0.2:
                    score += 10
                    oi_support = True
                    reasons.append(f"OI слегка растёт ({oi_value:+.2f}%)")
                elif oi_value <= -1.0:
                    oi_against = True
                    warnings.append(f"OI снижается ({oi_value:+.2f}%) — участие ослабевает")
                else:
                    warnings.append(f"OI почти не меняется ({oi_value:+.2f}%)")
            else:
                warnings.append("OI не получен")

            # Dynamic microstructure: repeated depth + micro-price + depth-flow proxy.
            micro = await self._microstructure(symbol)
            book_value = float(micro["bid_pct"]) if micro else None
            book_spread = float(micro["bid_range"]) if micro else -1.0
            book_aligned = False
            book_against = False
            depth_proxy = float(micro.get("depth_flow_proxy", 0.0)) if micro else 0.0
            if micro:
                book_aligned = book_value >= 52.0 if bullish else book_value <= 48.0
                book_against = book_value <= 47.0 if bullish else book_value >= 53.0
                stable = book_spread <= 4.0
                if book_aligned and stable:
                    score += 12
                    reasons.append(f"Стакан устойчиво поддерживает направление ({book_value:.1f}% bid)")
                elif book_aligned:
                    score += 5
                    warnings.append(f"Стакан поддерживает, но меняется ({book_spread:.1f} п.п.)")
                elif book_against:
                    warnings.append(f"Стакан против направления ({book_value:.1f}% bid)")
                else:
                    warnings.append(f"Стакан нейтрален ({book_value:.1f}% bid)")

                micro_aligned = micro["micro_offset"] >= 0.002 if bullish else micro["micro_offset"] <= -0.002
                if micro_aligned:
                    score += 3
                    reasons.append(f"Micro-price смещён в сторону {'LONG' if bullish else 'SHORT'}")

                proxy_aligned = depth_proxy >= 10.0 if bullish else depth_proxy <= -10.0
                proxy_against = depth_proxy <= -10.0 if bullish else depth_proxy >= 10.0
                if proxy_aligned:
                    score += 5
                    reasons.append(f"Поток ликвидности стакана поддерживает направление ({depth_proxy:+.1f}%)")
                elif proxy_against:
                    warnings.append(f"Поток ликвидности стакана против направления ({depth_proxy:+.1f}%)")
            else:
                warnings.append("Стакан не получен")

            # Absorption proxy: strong executed flow with unusually small price response.
            # It is explicitly a proxy, not a claim about hidden orders.
            one_return = (one_m[-1]["close"] / one_m[-2]["close"] - 1.0) * 100.0
            absorption = (
                flow_delta is not None
                and abs(flow_delta) >= 20.0
                and abs(one_return) <= 0.12
                and volume_ratio >= 1.15
            )
            if absorption:
                warnings.append("Есть признаки поглощения: сильный поток, но цена почти не продвинулась")
                score = max(0, score - 8)

            # Exhaustion proxy: price is still moving, but fresh flow/volume are fading.
            exhaustion = (
                abs(one_return) >= 0.20
                and volume_ratio < 1.0
                and (flow_delta is None or abs(flow_delta) < 8.0)
            )
            if exhaustion:
                warnings.append("Импульс может выдыхаться: цена движется, но свежие поток/объём слабеют")
                score = max(0, score - 8)

            direct_against = flow_against or book_against or (
                depth_proxy <= -10.0 if bullish else depth_proxy >= 10.0
            )
            continuation_evidence = (
                (latest["close"] >= local_high * 0.999 if bullish else latest["close"] <= local_low * 1.001)
                or early_aligned
            )
            fresh_flow = flow_aligned or (
                book_aligned and not book_against
            ) or (
                (depth_proxy >= 10.0 if bullish else depth_proxy <= -10.0)
            )
            strong = score >= 78 and continuation_evidence and fresh_flow and not direct_against and not absorption and not exhaustion
            confirmed = score >= 60 and continuation_evidence and fresh_flow and not direct_against and not absorption

            # A high sum cannot rescue a clear direct contradiction in flow/book.
            direct_against = flow_against or book_against
            primary_confirmed = score >= 60 and (flow_aligned or book_aligned) and not direct_against
            strong = primary_confirmed and score >= 78 and (flow_aligned and (book_aligned or oi_support))

            if strong:
                verdict = "СИЛЬНЫЙ ИМПУЛЬС"
            elif primary_confirmed:
                verdict = "ИМПУЛЬС ПОДТВЕРЖДЁН"
            elif score >= 48 and not direct_against and not oi_against:
                verdict = "ИМПУЛЬС ЕСТЬ, НО ПОДТВЕРЖДЕНИЕ СРЕДНЕЕ"
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
                    "orderbook_spread_pp": book_spread,
                    "depth_flow_proxy_pct": depth_proxy,
                    "microprice_offset_pct": micro.get("micro_offset", 0.0) if micro else 0.0,
                    "absorption_proxy": 1.0 if absorption else 0.0,
                    "exhaustion_proxy": 1.0 if exhaustion else 0.0,
                    "last_5m_body_pct": body,
                    "context_15m_body_pct": context_body,
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
                f"Flow delta: {m.get('trade_delta_pct', -999):+.1f}% · Volume: {m.get('volume_ratio', 0):.1f}x · OI: {m.get('oi_change_pct', -999):+.2f}%",
            ]
            if m.get("orderbook_bid_pct", -1) >= 0:
                lines.append(f"Стакан: {m['orderbook_bid_pct']:.1f}% bid · разброс {m.get('orderbook_spread_pp', 0):.1f} п.п.")
        if result.verdict == "СИЛЬНЫЙ ИМПУЛЬС":
            lines.append("➡️ Быстрая проверка сильно поддерживает основной сигнал. Это не гарантия движения.")
        elif result.verdict == "ИМПУЛЬС ПОДТВЕРЖДЁН":
            lines.append("➡️ Быстрая проверка поддерживает основной сигнал. Это не гарантия движения.")
        elif result.verdict == "ИМПУЛЬС ЕСТЬ, НО ПОДТВЕРЖДЕНИЕ СРЕДНЕЕ":
            lines.append("➡️ Импульс есть, но подтверждение недостаточно сильное для уверенного входа.")
        elif result.verdict == "НЕ ПОДТВЕРЖДЕНО":
            lines.append("➡️ Продолжение сейчас не подтверждено. Ничего не выдумываем.")
        else:
            lines.append("➡️ Дополнительные данные не получены. Основной сигнал не изменён.")
        return "\n".join(lines)
