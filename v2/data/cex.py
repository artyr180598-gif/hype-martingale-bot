"""
CEX-провайдер: Bybit V5 (основной) + Binance (резерв).

Нужен для двух вещей:
  1. настоящий стакан и свечи для токенов, которые уже листингованы на CEX
     (у DEX-пула стакана нет — там он эмулируется в v2/data/dex.py);
  2. WebSocket-поток тикеров/сделок — замена опроса REST в цикле. Поток
     складывает оборот и число сделок за последние 5 минут в TickerAggregator,
     а уровень 1 сканера берёт оттуда готовые метрики.

Если WS недоступен (нет aiohttp, провайдер закрыл соединение, DATA_MODE=demo),
вызывающий код использует REST-снимок — см. CexProvider.window_metrics().
"""

from __future__ import annotations

import time
from typing import Any

from v2.config import V2Config
from v2.core.logging import get_logger
from v2.core.monitor import monitor
from v2.data.provider import MarketProvider
from v2.data.ws_client import TickerAggregator, WebSocketStream
from v2.models import Candle, OrderBookLevel, OrderBookSnapshot, TokenCandidate, now_ms

logger = get_logger("data.cex")

BYBIT_REST = "https://api.bybit.com"
BYBIT_TESTNET_REST = "https://api-testnet.bybit.com"
BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
BYBIT_TESTNET_WS = "wss://stream-testnet.bybit.com/v5/public/linear"
BINANCE_REST = "https://api.binance.com"

TF_BYBIT = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "1d": "D"}
TF_BINANCE = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1d"}


class CexProvider(MarketProvider):
    name = "cex"

    def __init__(self, config: V2Config, http) -> None:
        self.config = config
        self.http = http
        self.aggregator = TickerAggregator(window_sec=300.0)
        self._streams: dict[str, WebSocketStream] = {}
        self.exchange = "bybit"

    # ── REST: свечи ──────────────────────────────────────────────
    async def klines(self, token: TokenCandidate, timeframe: str, limit: int = 300) -> list[Candle]:
        symbol = token.cex_symbol
        if not symbol:
            return []
        try:
            return await self._bybit_klines(symbol, timeframe, limit)
        except Exception as exc:  # noqa: BLE001 — уходим на резервную биржу
            monitor.record("data.cex.bybit_klines", exc)
            try:
                return await self._binance_klines(symbol, timeframe, limit)
            except Exception as exc2:  # noqa: BLE001
                monitor.record("data.cex.binance_klines", exc2)
                return []

    async def _bybit_klines(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        base = BYBIT_TESTNET_REST if self.config.BYBIT_TESTNET else BYBIT_REST
        payload = await self.http.get_json(
            f"{base}/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": TF_BYBIT.get(timeframe, "60"),
                "limit": min(limit, 1000),
            },
            component="data.cex.bybit",
        )
        rows = ((payload or {}).get("result") or {}).get("list") or []
        candles = [
            Candle(ts_ms=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
                   volume=float(r[5]))
            for r in rows
            if len(r) >= 6
        ]
        candles.sort(key=lambda c: c.ts_ms)  # Bybit отдаёт от новых к старым
        return candles

    async def _binance_klines(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        payload = await self.http.get_json(
            f"{BINANCE_REST}/api/v3/klines",
            params={"symbol": symbol, "interval": TF_BINANCE.get(timeframe, "1h"), "limit": min(limit, 1000)},
            component="data.cex.binance",
        )
        rows = payload or []
        return [
            Candle(ts_ms=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
                   volume=float(r[5]))
            for r in rows
            if len(r) >= 6
        ]

    # ── REST: стакан ─────────────────────────────────────────────
    async def orderbook(self, token: TokenCandidate, depth: int = 50) -> OrderBookSnapshot | None:
        symbol = token.cex_symbol
        if not symbol:
            return None
        try:
            base = BYBIT_TESTNET_REST if self.config.BYBIT_TESTNET else BYBIT_REST
            payload = await self.http.get_json(
                f"{base}/v5/market/orderbook",
                params={"category": "linear", "symbol": symbol, "limit": min(max(depth, 1), 200)},
                component="data.cex.bybit",
            )
            result = (payload or {}).get("result") or {}
            bids = [OrderBookLevel(price=float(p), qty=float(q)) for p, q in result.get("b") or []]
            asks = [OrderBookLevel(price=float(p), qty=float(q)) for p, q in result.get("a") or []]
            if bids or asks:
                return OrderBookSnapshot(
                    symbol=symbol, bids=bids, asks=asks, ts_ms=now_ms(), source="bybit-ws-rest"
                )
        except Exception as exc:  # noqa: BLE001
            monitor.record("data.cex.bybit_book", exc)

        try:
            payload = await self.http.get_json(
                f"{BINANCE_REST}/api/v3/depth",
                params={"symbol": symbol, "limit": min(max(depth, 5), 500)},
                component="data.cex.binance",
            )
            bids = [OrderBookLevel(price=float(p), qty=float(q)) for p, q in (payload or {}).get("bids") or []]
            asks = [OrderBookLevel(price=float(p), qty=float(q)) for p, q in (payload or {}).get("asks") or []]
            if not (bids or asks):
                return None
            return OrderBookSnapshot(symbol=symbol, bids=bids, asks=asks, ts_ms=now_ms(), source="binance")
        except Exception as exc:  # noqa: BLE001
            monitor.record("data.cex.binance_book", exc)
            return None

    # ── WebSocket ────────────────────────────────────────────────
    async def ticker_stream(self, symbols: list[str]) -> WebSocketStream | None:
        """
        Запускает WS-поток сделок по списку символов.

        Подписка на publicTrade.<SYMBOL> даёт каждую сделку, из которой мы
        считаем оборот и количество сделок за 5 минут (метрики уровня 1).
        """
        if not self.config.USE_WEBSOCKET or not symbols:
            return None
        url = BYBIT_TESTNET_WS if self.config.BYBIT_TESTNET else BYBIT_WS
        args = [f"publicTrade.{s.upper()}" for s in symbols[:50]] + ["tickers.BTCUSDT"]
        stream = WebSocketStream(
            self.config,
            url,
            name="bybit-public",
            subscribe_payload={"op": "subscribe", "args": args},
            on_message=self._on_ws_message,
        )
        await stream.start()
        self._streams["bybit-public"] = stream
        logger.info("WS-поток запущен: %d символов", len(symbols))
        return stream

    async def _on_ws_message(self, data: dict[str, Any]) -> None:
        topic = str(data.get("topic") or "")
        payload = data.get("data")
        if topic.startswith("publicTrade.") and isinstance(payload, list):
            symbol = topic.split(".", 1)[1]
            for trade in payload:
                try:
                    price = float(trade.get("p") or 0)
                    qty = float(trade.get("v") or 0)
                except (TypeError, ValueError):
                    continue
                await self.aggregator.add_trade(symbol, price * qty, 1)
        elif topic.startswith("tickers.") and isinstance(payload, dict):
            symbol = str(payload.get("symbol") or topic.split(".", 1)[1])
            turnover = float(payload.get("turnover24h") or 0)
            await self.aggregator.add_snapshot(symbol + ":24h", turnover, 0)

    async def window_metrics(self, symbol: str) -> tuple[float, int]:
        """
        Оборот и число сделок за 5 минут.

        Если WS-поток жив — берём из агрегатора (точные данные). Иначе
        оцениваем по последним сделкам через REST: берём 200 сделок и считаем
        оборот за последние 5 минут — точность ниже, но сканер не простаивает.
        """
        volume, trades = await self.aggregator.snapshot(symbol.upper())
        if trades > 0:
            return volume, trades
        try:
            base = BYBIT_TESTNET_REST if self.config.BYBIT_TESTNET else BYBIT_REST
            payload = await self.http.get_json(
                f"{base}/v5/market/recent-trade",
                params={"category": "linear", "symbol": symbol.upper(), "limit": 200},
                component="data.cex.recent_trade",
            )
            rows = ((payload or {}).get("result") or {}).get("list") or []
            cutoff = now_ms() - 300_000
            total = 0.0
            count = 0
            for row in rows:
                ts = int(row.get("T") or 0)
                if ts < cutoff:
                    continue
                total += float(row.get("p") or 0) * float(row.get("v") or 0)
                count += 1
            return total, count
        except Exception as exc:  # noqa: BLE001
            monitor.record("data.cex.window", exc)
            return 0.0, 0

    # ── поиск CEX-пары для DEX-токена ────────────────────────────
    async def cex_symbol_for(self, base_asset: str) -> str:
        """Возвращает пару вида {BASE}USDT, если она торгуется на Bybit."""
        symbol = f"{base_asset.upper()}USDT"
        try:
            base = BYBIT_TESTNET_REST if self.config.BYBIT_TESTNET else BYBIT_REST
            payload = await self.http.get_json(
                f"{base}/v5/market/tickers",
                params={"category": "linear", "symbol": symbol},
                component="data.cex.tickers",
            )
            items = ((payload or {}).get("result") or {}).get("list") or []
            return symbol if items else ""
        except Exception as exc:  # noqa: BLE001
            monitor.record("data.cex.tickers", exc)
            return ""

    async def close(self) -> None:
        for stream in self._streams.values():
            await stream.stop()
        self._streams.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "streams": {k: {"messages": s.messages, "reconnects": s.reconnects} for k, s in self._streams.items()},
        }
