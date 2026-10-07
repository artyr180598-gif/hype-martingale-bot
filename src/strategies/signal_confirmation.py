from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import aiohttp

log = logging.getLogger(__name__)

BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
BYBIT_OI_URL = "https://api.bybit.com/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=5min&limit=4"


@dataclass
class ConfirmationResult:
    symbol: str
    direction: str
    verdict: str
    score: int
    reasons: list[str]
    warnings: list[str]
    metrics: dict[str, float] = field(default_factory=dict)


class SignalConfirmation:
    """Realtime confirmation for one already-detected Pump/Dump candidate.

    The primary scanner remains the trigger. As soon as a candidate arrives,
    this layer opens a short-lived Bybit public WebSocket session for ONLY that
    symbol and observes real orderbook deltas and executed trades. REST is used
    only for closed-candle/context data and OI.

    This is real event-stream monitoring, not a simulated OFI calculation.
    """

    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self.observe_seconds = 10.0

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )

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

    async def _oi_change(self, symbol: str) -> float | None:
        try:
            data = await self._get(BYBIT_OI_URL.format(symbol=symbol))
            rows = data.get("result", {}).get("list", [])
            if len(rows) < 2:
                return None
            newest = float(
                rows[0].get("singleOpenInterest")
                or rows[0].get("openInterest")
                or 0
            )
            oldest = float(
                rows[-1].get("singleOpenInterest")
                or rows[-1].get("openInterest")
                or 0
            )
            return (newest / oldest - 1.0) * 100.0 if oldest else None
        except Exception:
            return None

    async def _realtime_sample(self, symbol: str, direction: str) -> dict:
        """Observe Bybit orderbook deltas + public trades for a short window.

        Level-50 orderbook starts with a snapshot and then receives deltas.
        We maintain the local book and calculate true event-level order-flow
        imbalance from successive bid/ask size changes. Executed trade delta is
        calculated from the taker side of each public trade.
        """
        if not self.session:
            raise RuntimeError("SignalConfirmation is not started")

        bids: dict[float, float] = {}
        asks: dict[float, float] = {}
        prev_bids: dict[float, float] | None = None
        prev_asks: dict[float, float] | None = None
        ofi_buy = 0.0
        ofi_sell = 0.0
        buy_value = 0.0
        sell_value = 0.0
        trade_count = 0
        book_events = 0
        sequence_gap = False
        snapshot_ready = False
        last_u: int | None = None
        first_mid: float | None = None
        last_mid: float | None = None
        micro_offsets: list[float] = []

        async def apply_book(data: dict, msg_type: str) -> None:
            nonlocal bids, asks, prev_bids, prev_asks, snapshot_ready
            nonlocal book_events, sequence_gap, last_u, first_mid, last_mid

            u = data.get("u")
            if isinstance(u, int):
                if msg_type == "snapshot" or u == 1:
                    bids = {float(p): float(q) for p, q in data.get("b", []) if float(q) > 0}
                    asks = {float(p): float(q) for p, q in data.get("a", []) if float(q) > 0}
                    last_u = u
                    snapshot_ready = True
                elif snapshot_ready:
                    if last_u is not None and u > last_u + 1:
                        sequence_gap = True
                    for p, q in data.get("b", []):
                        price, qty = float(p), float(q)
                        if qty == 0:
                            bids.pop(price, None)
                        else:
                            bids[price] = qty
                    for p, q in data.get("a", []):
                        price, qty = float(p), float(q)
                        if qty == 0:
                            asks.pop(price, None)
                        else:
                            asks[price] = qty
                    last_u = u

            if not bids or not asks:
                return

            best_bid = max(bids)
            best_ask = min(asks)
            mid = (best_bid + best_ask) / 2.0
            last_mid = mid
            if first_mid is None:
                first_mid = mid

            if prev_bids is not None and prev_asks is not None:
                # Event-level OFI using price-level queue changes.
                bid_flow = 0.0
                ask_flow = 0.0
                for price in set(prev_bids) | set(bids):
                    old = prev_bids.get(price, 0.0)
                    new = bids.get(price, 0.0)
                    if price >= best_bid:
                        bid_flow += new - old
                for price in set(prev_asks) | set(asks):
                    old = prev_asks.get(price, 0.0)
                    new = asks.get(price, 0.0)
                    if price <= best_ask:
                        ask_flow += new - old
                # Positive OFI = bid-side strengthening / ask-side weakening.
                signed = bid_flow - ask_flow
                if signed >= 0:
                    ofi_buy += signed
                else:
                    ofi_sell += -signed

            prev_bids = dict(bids)
            prev_asks = dict(asks)
            bid_value = sum(p * q for p, q in list(bids.items())[:50])
            ask_value = sum(p * q for p, q in list(asks.items())[:50])
            total = bid_value + ask_value
            if total:
                micro = (best_ask * bid_value + best_bid * ask_value) / total
                micro_offsets.append((micro - mid) / mid * 100.0)
            book_events += 1

        async def consume(ws: aiohttp.ClientWebSocketResponse) -> None:
            nonlocal buy_value, sell_value, trade_count
            while True:
                msg = await ws.receive(timeout=2.5)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    payload = json.loads(msg.data)
                    topic = payload.get("topic", "")
                    if topic.startswith("orderbook.50."):
                        await apply_book(
                            payload.get("data", {}),
                            str(payload.get("type", "delta")),
                        )
                    elif topic.startswith("publicTrade."):
                        for row in payload.get("data", []):
                            try:
                                side = str(row.get("S", "")).lower()
                                price = float(row.get("p", 0))
                                size = float(row.get("v", 0))
                                if price <= 0 or size <= 0:
                                    continue
                                value = price * size
                                if side == "buy":
                                    buy_value += value
                                elif side == "sell":
                                    sell_value += value
                                else:
                                    continue
                                trade_count += 1
                            except (TypeError, ValueError):
                                continue
                elif msg.type in {
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                }:
                    break

        try:
            async with self.session.ws_connect(
                BYBIT_WS_URL,
                heartbeat=20,
                receive_timeout=3.0,
                timeout=5.0,
            ) as ws:
                await ws.send_json(
                    {
                        "op": "subscribe",
                        "args": [
                            f"orderbook.50.{symbol}",
                            f"publicTrade.{symbol}",
                        ],
                    }
                )
                deadline = time.monotonic() + self.observe_seconds
                while time.monotonic() < deadline:
                    remaining = max(0.2, deadline - time.monotonic())
                    try:
                        msg = await ws.receive(timeout=min(2.5, remaining))
                    except asyncio.TimeoutError:
                        continue
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        payload = json.loads(msg.data)
                        topic = payload.get("topic", "")
                        if topic.startswith("orderbook.50."):
                            await apply_book(
                                payload.get("data", {}),
                                str(payload.get("type", "delta")),
                            )
                        elif topic.startswith("publicTrade."):
                            for row in payload.get("data", []):
                                try:
                                    side = str(row.get("S", "")).lower()
                                    price = float(row.get("p", 0))
                                    size = float(row.get("v", 0))
                                    if price <= 0 or size <= 0:
                                        continue
                                    value = price * size
                                    if side == "buy":
                                        buy_value += value
                                    elif side == "sell":
                                        sell_value += value
                                    else:
                                        continue
                                    trade_count += 1
                                except (TypeError, ValueError):
                                    continue
                        elif msg.type in {
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.ERROR,
                        }:
                            break
        except Exception as exc:
            log.warning("Realtime confirmation WS failed for %s: %s", symbol, type(exc).__name__)

        total_trade = buy_value + sell_value
        trade_delta = (
            (buy_value - sell_value) / total_trade * 100.0
            if total_trade > 0
            else None
        )
        total_ofi = ofi_buy + ofi_sell
        ofi_delta = (
            (ofi_buy - ofi_sell) / total_ofi * 100.0
            if total_ofi > 0
            else None
        )
        if bids and asks:
            bid_value = sum(p * q for p, q in sorted(bids.items(), reverse=True)[:50])
            ask_value = sum(p * q for p, q in sorted(asks.items())[:50])
            book_bid_pct = bid_value / (bid_value + ask_value) * 100.0 if bid_value + ask_value else None
        else:
            book_bid_pct = None
        price_change = (
            (last_mid / first_mid - 1.0) * 100.0
            if first_mid and last_mid
            else None
        )

        return {
            "trade_delta_pct": trade_delta,
            "ofi_delta_pct": ofi_delta,
            "trade_count": float(trade_count),
            "book_bid_pct": book_bid_pct,
            "book_events": float(book_events),
            "sequence_gap": 1.0 if sequence_gap else 0.0,
            "price_change_pct": price_change,
            "microprice_offset_pct": (
                sum(micro_offsets) / len(micro_offsets)
                if micro_offsets else 0.0
            ),
        }

    async def check(self, symbol: str, direction: str) -> ConfirmationResult:
        try:
            one_m, five_m, fifteen_m, oi = await asyncio.gather(
                self._klines(symbol, "1", 70),
                self._klines(symbol, "5", 70),
                self._klines(symbol, "15", 35),
                self._oi_change(symbol),
                return_exceptions=True,
            )
            if any(isinstance(x, Exception) for x in (one_m, five_m, fifteen_m)):
                raise RuntimeError("kline data unavailable")
            if len(one_m) < 30 or len(five_m) < 25 or len(fifteen_m) < 15:
                raise RuntimeError("not enough closed candles")

            realtime = await self._realtime_sample(symbol, direction)
            bullish = direction == "PUMP"
            score = 0
            reasons: list[str] = []
            warnings: list[str] = []

            window = five_m[-13:-1]
            latest = five_m[-1]
            previous = five_m[-2]
            local_high = max(x["high"] for x in window)
            local_low = min(x["low"] for x in window)
            body = (latest["close"] - latest["open"]) / latest["open"] * 100.0

            if bullish:
                breakout = latest["close"] >= local_high * 0.999
                continuation = latest["high"] > previous["high"] and latest["low"] >= previous["low"]
                structure_aligned = breakout or continuation
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
                structure_aligned = breakdown or continuation
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

            context = fifteen_m[-1]
            context_body = (context["close"] - context["open"]) / context["open"] * 100.0
            context_aligned = context_body > 0 if bullish else context_body < 0
            if context_aligned:
                score += 5
                reasons.append(f"15m контекст совпадает с направлением ({context_body:+.2f}%)")
            elif abs(context_body) >= 0.35:
                warnings.append(f"15m контекст пока против направления ({context_body:+.2f}%)")

            flow = realtime.get("trade_delta_pct")
            ofi = realtime.get("ofi_delta_pct")
            book = realtime.get("book_bid_pct")
            price_change = realtime.get("price_change_pct")

            flow_aligned = flow is not None and (flow >= 8.0 if bullish else flow <= -8.0)
            flow_against = flow is not None and (flow <= -8.0 if bullish else flow >= 8.0)
            ofi_aligned = ofi is not None and (ofi >= 10.0 if bullish else ofi <= -10.0)
            ofi_against = ofi is not None and (ofi <= -10.0 if bullish else ofi >= 10.0)
            book_aligned = book is not None and (book >= 52.0 if bullish else book <= 48.0)
            book_against = book is not None and (book <= 47.0 if bullish else book >= 53.0)

            if flow_aligned:
                score += 20
                reasons.append(f"Realtime taker-flow подтверждает: delta {flow:+.1f}%")
            elif flow_against:
                warnings.append(f"Realtime taker-flow против направления: delta {flow:+.1f}%")
            elif flow is not None:
                score += 5
                reasons.append(f"Realtime taker-flow нейтрален: delta {flow:+.1f}%")
            else:
                warnings.append("Realtime сделки не получены")

            if ofi_aligned:
                score += 20
                reasons.append(f"Настоящий event-level OFI подтверждает направление: {ofi:+.1f}%")
            elif ofi_against:
                warnings.append(f"Event-level OFI против направления: {ofi:+.1f}%")
            elif ofi is None:
                warnings.append("Event-level OFI не получен")

            if book_aligned:
                score += 12
                reasons.append(f"Realtime стакан поддерживает направление: {book:.1f}% bid")
            elif book_against:
                warnings.append(f"Realtime стакан против направления: {book:.1f}% bid")
            elif book is not None:
                reasons.append(f"Realtime стакан нейтрален: {book:.1f}% bid")
            else:
                warnings.append("Realtime стакан не получен")

            oi_value = float(oi) if isinstance(oi, (int, float)) else None
            oi_support = oi_value is not None and oi_value >= 0.8
            oi_against = oi_value is not None and oi_value <= -1.0
            if oi_support:
                score += 12
                reasons.append(f"OI растёт вместе с движением ({oi_value:+.2f}%)")
            elif oi_against:
                warnings.append(f"OI снижается ({oi_value:+.2f}%)")
            elif oi_value is not None:
                score += 4
                reasons.append(f"OI без сильного изменения ({oi_value:+.2f}%)")
            else:
                warnings.append("OI не получен")

            volumes = [x["volume"] for x in one_m]
            avg_volume = sum(volumes[-21:-1]) / 20.0
            volume_ratio = volumes[-1] / avg_volume if avg_volume else 1.0
            if volume_ratio >= 1.35:
                score += 8
                reasons.append(f"1m объём расширен до {volume_ratio:.1f}x среднего")
            elif volume_ratio < 0.85:
                warnings.append(f"1m объём слабый ({volume_ratio:.1f}x)")

            absorption = (
                flow is not None
                and abs(flow) >= 20.0
                and price_change is not None
                and abs(price_change) <= 0.08
                and volume_ratio >= 1.15
            )
            exhaustion = (
                price_change is not None
                and abs(price_change) >= 0.12
                and volume_ratio < 1.0
                and (flow is None or abs(flow) < 8.0)
            )
            if absorption:
                warnings.append("Поглощение-прокси: сильные сделки, но цена почти не продвинулась")
                score -= 8
            if exhaustion:
                warnings.append("Признаки истощения: цена движется, но свежий поток/объём слабеют")
                score -= 8

            # A confirmation cannot be strong when the live flow directly contradicts it.
            direct_against = flow_against or ofi_against or book_against
            fresh_flow = flow_aligned or ofi_aligned or book_aligned
            primary_confirmed = (
                score >= 60
                and structure_aligned
                and fresh_flow
                and not direct_against
                and not absorption
            )
            strong = (
                score >= 78
                and structure_aligned
                and flow_aligned
                and ofi_aligned
                and not direct_against
                and not absorption
                and not exhaustion
            )

            if strong:
                verdict = "СИЛЬНЫЙ ИМПУЛЬС"
            elif primary_confirmed:
                verdict = "ИМПУЛЬС ПОДТВЕРЖДЁН"
            elif score >= 48 and structure_aligned and not direct_against and not oi_against:
                verdict = "ИМПУЛЬС ЕСТЬ, НО ПОДТВЕРЖДЕНИЕ СРЕДНЕЕ"
            else:
                verdict = "НЕ ПОДТВЕРЖДЕНО"

            return ConfirmationResult(
                symbol=symbol,
                direction=direction,
                verdict=verdict,
                score=max(0, min(score, 100)),
                reasons=reasons,
                warnings=warnings,
                metrics={
                    "observe_seconds": self.observe_seconds,
                    "trade_delta_pct": flow if flow is not None else -999.0,
                    "ofi_delta_pct": ofi if ofi is not None else -999.0,
                    "trade_count": realtime.get("trade_count", 0.0),
                    "orderbook_events": realtime.get("book_events", 0.0),
                    "orderbook_bid_pct": book if book is not None else -1.0,
                    "price_change_pct": price_change if price_change is not None else -999.0,
                    "oi_change_pct": oi_value if oi_value is not None else -999.0,
                    "volume_ratio": volume_ratio,
                    "sequence_gap": realtime.get("sequence_gap", 0.0),
                    "microprice_offset_pct": realtime.get("microprice_offset_pct", 0.0),
                    "structure_aligned": 1.0 if structure_aligned else 0.0,
                    "absorption_proxy": 1.0 if absorption else 0.0,
                    "exhaustion_proxy": 1.0 if exhaustion else 0.0,
                },
            )
        except Exception as exc:
            log.warning("Realtime confirmation failed for %s: %s", symbol, type(exc).__name__)
            return ConfirmationResult(
                symbol=symbol,
                direction=direction,
                verdict="ПРОВЕРКА НЕ ПОЛУЧЕНА",
                score=0,
                reasons=[],
                warnings=[
                    "Realtime подтверждение не получено; основной Pump/Dump сигнал не изменён."
                ],
                metrics={},
            )

    @staticmethod
    def format_result(result: ConfirmationResult, label: str = "REALTIME ПРОВЕРКА") -> str:
        icon = "🟢" if result.direction == "PUMP" else "🔴"
        lines = [
            f"{icon} ⚡ {label} · {result.symbol.removesuffix('USDT')}",
            f"Импульс: {'PUMP → LONG' if result.direction == 'PUMP' else 'DUMP → SHORT'}",
            f"Сила подтверждения: {result.score}/100",
            f"Статус: {result.verdict}",
        ]
        if result.reasons:
            lines += ["", "✅ Поддерживает:"] + [f"• {x}" for x in result.reasons[:6]]
        if result.warnings:
            lines += ["", "⚠️ Слабые места:"] + [f"• {x}" for x in result.warnings[:5]]
        m = result.metrics
        if m:
            lines += [
                "",
                f"Realtime: {m.get('observe_seconds', 0):.0f}с · trades {m.get('trade_count', 0):.0f} · OB events {m.get('orderbook_events', 0):.0f}",
                f"Taker delta: {m.get('trade_delta_pct', -999):+.1f}% · OFI: {m.get('ofi_delta_pct', -999):+.1f}%",
                f"Стакан: {m.get('orderbook_bid_pct', -1):.1f}% bid · Price: {m.get('price_change_pct', -999):+.3f}%",
                f"Volume: {m.get('volume_ratio', 0):.1f}x · OI: {m.get('oi_change_pct', -999):+.2f}%",
            ]
            if m.get("sequence_gap", 0) > 0:
                lines.append("⚠️ В realtime orderbook обнаружен разрыв последовательности — OFI может быть неполным.")
        if result.verdict == "СИЛЬНЫЙ ИМПУЛЬС":
            lines.append("➡️ Realtime-поток сильно подтверждает основной сигнал. Это не гарантия движения.")
        elif result.verdict == "ИМПУЛЬС ПОДТВЕРЖДЁН":
            lines.append("➡️ Realtime-поток подтверждает основной сигнал. Это не гарантия движения.")
        elif result.verdict == "ИМПУЛЬС ЕСТЬ, НО ПОДТВЕРЖДЕНИЕ СРЕДНЕЕ":
            lines.append("➡️ Импульс есть, но realtime-подтверждение пока недостаточно сильное.")
        elif result.verdict == "НЕ ПОДТВЕРЖДЕНО":
            lines.append("➡️ Продолжение сейчас не подтверждено. Ничего не выдумываем.")
        else:
            lines.append("➡️ Дополнительные realtime-данные не получены. Основной сигнал не изменён.")
        return "\n".join(lines)
