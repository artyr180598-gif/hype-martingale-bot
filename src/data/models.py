"""
Типизированные модели рыночных данных.

Единый контракт для всех источников (Bybit / Binance / MEXC / CoinGecko /
демо-рынок). Такой подход взят из ccxt: один набор сущностей, разные
коннекторы бирж. Отдельно описаны precision/limits инструмента — без них
невозможно сказать новичку «купи на 47.3 USDT», биржа такой ордер отклонит.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Точность отображения цен по умолчанию (если биржа не дала свою) ──
DEFAULT_TICK_SIZE = 0.0001
DEFAULT_QTY_STEP = 0.0001


@dataclass
class Instrument:
    """Торговый инструмент (спот-пара или бессрочный фьючерс)."""

    symbol: str                       # BTCUSDT
    base: str                         # BTC
    quote: str = "USDT"               # USDT
    category: str = "linear"          # spot | linear | inverse
    status: str = "Trading"           # Trading | Settling | Closed
    price_scale: int = 4              # знаков после запятой у цены
    qty_scale: int = 4                # знаков после запятой у количества
    tick_size: float = DEFAULT_TICK_SIZE
    qty_step: float = DEFAULT_QTY_STEP
    min_qty: float = 0.0
    min_notional: float = 5.0         # минимальный объём ордера в quote
    max_leverage: int = 50
    maker_fee: float = 0.0002
    taker_fee: float = 0.00055
    turnover_24h: float = 0.0

    @property
    def is_spot(self) -> bool:
        return self.category == "spot"

    @property
    def is_futures(self) -> bool:
        return self.category in ("linear", "inverse")

    def round_price(self, price: float) -> float:
        return round(price, max(self.price_scale, 0))

    def round_qty(self, qty: float) -> float:
        if self.qty_step > 0:
            import math

            qty = math.floor(qty / self.qty_step) * self.qty_step
        return round(qty, max(self.qty_scale, 0))


@dataclass
class Ticker:
    """Тикер: последняя цена и суточная статистика."""

    symbol: str
    last: float
    price_24h_pct: float = 0.0
    turnover_24h: float = 0.0         # оборот в quote (USDT)
    volume_24h: float = 0.0           # объём в base (BTC)
    high_24h: float = 0.0
    low_24h: float = 0.0
    open_24h: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    funding_rate: float | None = None
    next_funding_ms: int | None = None
    open_interest: float | None = None
    open_interest_usd: float | None = None
    mark_price: float | None = None
    index_price: float | None = None
    ts_ms: int = 0

    @property
    def spread_pct(self) -> float:
        if self.bid and self.ask:
            return (self.ask - self.bid) / self.ask * 100
        return 0.0


@dataclass
class FundingEntry:
    """Запись истории ставок финансирования."""

    ts_ms: int
    rate: float
    symbol: str = ""


@dataclass
class Liquidation:
    """Принудительная ликвидация позиции."""

    symbol: str
    side: str                         # Buy | Sell  (сторона ЛИКВИДИРОВАННОЙ позиции)
    size: float                       # объём в quote (USDT)
    qty: float = 0.0
    price: float = 0.0
    ts_ms: int = 0


@dataclass
class OrderBook:
    """Стакан: глубина, спред, стены и перекос."""

    symbol: str
    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, qty)
    asks: list[tuple[float, float]] = field(default_factory=list)
    ts_ms: int = 0

    @property
    def mid(self) -> float:
        if self.bids and self.asks:
            return (self.bids[0][0] + self.asks[0][0]) / 2
        if self.bids:
            return self.bids[0][0]
        if self.asks:
            return self.asks[0][0]
        return 0.0

    @property
    def spread(self) -> float:
        if self.bids and self.asks:
            return self.asks[0][0] - self.bids[0][0]
        return 0.0

    @property
    def spread_pct(self) -> float:
        mid = self.mid
        return (self.spread / mid * 100) if mid else 0.0

    def depth(self, pct: float = 1.0) -> tuple[float, float]:
        """Суммарный объём (в quote) в пределах ±pct% от mid."""
        mid = self.mid
        if not mid:
            return 0.0, 0.0
        bid_usd = sum(p * q for p, q in self.bids if p >= mid * (1 - pct / 100))
        ask_usd = sum(p * q for p, q in self.asks if p <= mid * (1 + pct / 100))
        return bid_usd, ask_usd

    @property
    def imbalance(self) -> float:
        """Перекос стакана: +1 — вся глубина в бидах, -1 — в асках."""
        bid_usd, ask_usd = self.depth(1.0)
        total = bid_usd + ask_usd
        if total <= 0:
            return 0.0
        return (bid_usd - ask_usd) / total

    def walls(self, top_n: int = 3) -> dict:
        """Крупнейшие уровни-стены с обеих сторон."""
        bids = sorted(self.bids, key=lambda x: x[0] * x[1], reverse=True)[:top_n]
        asks = sorted(self.asks, key=lambda x: x[0] * x[1], reverse=True)[:top_n]
        return {
            "bid_walls": [{"price": p, "usd": p * q} for p, q in bids],
            "ask_walls": [{"price": p, "usd": p * q} for p, q in asks],
        }


@dataclass
class CoinMover:
    """Монета из спотового источника (CoinGecko): мувер или тренд."""

    symbol: str                       # XXXUSDT — приведено к биржевому виду
    name: str = ""
    rank: int = 999
    price: float = 0.0
    price_24h_pct: float = 0.0
    volume_24h: float = 0.0
    market_cap: float | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "rank": self.rank,
            "price": self.price,
            "price_24h_pct": round(self.price_24h_pct, 2),
            "volume_24h": self.volume_24h,
            "market_cap": self.market_cap,
        }


@dataclass
class NewsItem:
    """Новость с простой лексической оценкой сентимента."""

    id: str
    ts_ms: int
    source: str
    title: str
    url: str = ""
    symbols: list[str] = field(default_factory=list)
    sentiment: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts_ms": self.ts_ms,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "symbols": self.symbols,
            "sentiment": round(self.sentiment, 3),
        }


@dataclass
class FearGreed:
    """Индекс страха и жадности (0..100)."""

    value: int
    classification: str
    ts_ms: int = 0

    def to_dict(self) -> dict:
        return {"value": self.value, "classification": self.classification, "ts_ms": self.ts_ms}


@dataclass
class GlobalStats:
    """Общая картина рынка."""

    total_market_cap_usd: float = 0.0
    total_volume_24h_usd: float = 0.0
    btc_dominance: float = 0.0
    eth_dominance: float = 0.0
    market_cap_change_24h_pct: float = 0.0
    fear_greed: FearGreed | None = None
    ts_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "total_market_cap_usd": self.total_market_cap_usd,
            "total_volume_24h_usd": self.total_volume_24h_usd,
            "btc_dominance": round(self.btc_dominance, 2),
            "eth_dominance": round(self.eth_dominance, 2),
            "market_cap_change_24h_pct": round(self.market_cap_change_24h_pct, 2),
            "fear_greed": self.fear_greed.to_dict() if self.fear_greed else None,
            "ts_ms": self.ts_ms,
        }


def normalize_symbol(raw: str, quote: str = "USDT") -> str:
    """'BTC/USDT', 'btc-usdt', 'BTC' → 'BTCUSDT' (как в ccxt: единый формат)."""
    s = str(raw).strip().upper().replace("/", "").replace("-", "").replace("_", "").replace(":", "")
    if s.endswith("PERP"):
        s = s[:-4]
    if not s.endswith(quote) and len(s) >= 2:
        s += quote
    return s


def base_of(symbol: str) -> str:
    return symbol[:-4] if symbol.endswith("USDT") else symbol
