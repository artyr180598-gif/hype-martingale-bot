"""Domain models for the v3 USDT-perp futures signal engine.

Objects here are plain dataclasses (fast, typed, easy to persist).  ``None``
means "unknown / not available" -- it is deliberately different from ``0``,
because a zero spread/liquidity is a hard filter while a missing field is only
a data-confidence penalty.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Direction = Literal["LONG", "SHORT", "WAIT", "NO_TRADE"]
SignalStatus = Literal[
    "GENERATED", "CONFIRMED", "ACTIVE", "TP1_HIT", "TP2_HIT", "TP3_HIT",
    "CLOSED", "STOPPED", "INVALIDATED", "EXPIRED", "NO_TRADE",
]
MarketRegime = Literal[
    "TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY",
    "LOW_VOLATILITY", "BREAKOUT", "BREAKDOWN", "ACCUMULATION",
    "DISTRIBUTION", "UNCERTAIN",
]
Tier = Literal["S", "A", "B", "C", "NONE"]


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class DataBundle:
    """Everything the signal engine needs about one symbol at one moment."""

    symbol: str
    ts_ms: int = field(default_factory=now_ms)
    price: float = 0.0
    price_24h_pct: float = 0.0
    turnover_24h: float = 0.0
    volume_24h: float = 0.0
    spread_pct: float | None = None
    funding_rate: float | None = None
    funding_history: list[float] = field(default_factory=list)
    open_interest_usd: float | None = None
    # (timestamp_ms, raw OI in USD); use oi_change_24h_pct for the percentage.
    open_interest_history: list[tuple[float, float]] = field(default_factory=list)
    oi_change_24h_pct: float | None = None
    long_short_ratio: float | None = None          # 0..1 (Bybit account ratio)
    mark_price: float | None = None
    index_price: float | None = None
    liquidations: list[dict[str, Any]] = field(default_factory=list)
    orderbook: dict[str, Any] | None = None
    btc_price_24h_pct: float | None = None
    btc_turnover_24h: float | None = None
    btc_funding_rate: float | None = None
    eth_price_24h_pct: float | None = None
    eth_funding_rate: float | None = None
    global_change_pct: float | None = None
    btc_dominance: float | None = None
    news_sentiment: float | None = None
    news_items: list[dict[str, Any]] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    data_age_seconds: float | None = None
    symbol_price_history: list[float] = field(default_factory=list)
    symbol_volume_history: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["orderbook"] = self.orderbook
        return d


@dataclass
class TimeframeView:
    timeframe: str
    trend: str                                  # up | down | range
    adx: float
    rsi: float
    macd_hist: float
    stoch_k: float
    atr: float
    atr_pct: float
    atr_pctl: float
    vol_z: float
    cvd_trend: float
    obv_trend: float
    squeeze: bool
    supertrend: int
    vwap_dist_pct: float
    support: float | None
    resistance: float | None
    last_swing_high: float | None
    last_swing_low: float | None
    structure_signal: str                       # BOS_UP | CHoCH_UP | BOS_DOWN | ... | none
    # ── раунд 4: экспонируем уже посчитанные индикаторы (они были «мёртвыми») ──
    plus_di: float = 0.0
    minus_di: float = 0.0
    ema_stack: int = 0                          # -3..+3: ema9/20/50/200 выравнивание
    mfi: float = 50.0                           # money flow index
    bb_pctb: float = 0.5                        # Bollinger %B
    wpr: float = -50.0                          # Williams %R
    roc20: float = 0.0
    macd_cross: int = 0                         # 1 свежий бычий, -1 медвежий
    stoch_cross: int = 0                        # 1 бычий, -1 медвежий
    rvol: float = 1.0                           # объём последнего бара / среднее окна
    score: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EmergenceSnapshot:
    """«Намечающееся движение» до того, как оно разгорелось (ранний отбор).

    Это признак для ранжирования/объяснения, НЕ триггер и НЕ гейт: направление
    всегда остаётся за ``FuturesSignalEngine`` и детерминированным gate.
    """

    enabled: bool = True
    rvol: float = 1.0                           # объём последнего закрытого бара / среднее окна
    volume_acceleration: float = 1.0            # RVOL последнего бара / RVOL предыдущего бара
    squeeze: bool = False                       # сжатие волатильности сейчас
    squeeze_release: bool = False               # сжатие было недавно и полосы расширяются
    consolidation: bool = False                 # узкий диапазон при нормальном ATR
    compression_ratio: float = 1.0              # текущий ATR / типичный ATR (меньше 1 = сжатие)
    range_width_atr: float = 0.0                # ширина недавнего коридора в ATR
    breakout_pressure: float = 0.0              # -1..+1: давление продавцов/покупателей в последнем баре
    rs24: float = 0.0                           # 24h движение относительно BTC
    dpos: float = 0.5                           # позиция цены в 24h диапазоне (0..1)
    room_pct: float = 0.0                       # запас до границы диапазона в предполагаемом направлении
    oi_build_pct: float | None = None           # рост OI за 24h (если есть история)
    funding_neutral: bool = True
    near_breakout: bool = False                 # у вершины диапазона на нарастающем объёме
    near_breakdown: bool = False                # у дна диапазона на растущих продажах
    phase: str = "NEUTRAL"                     # EARLY | TRIGGERED | EXHAUSTED | NEUTRAL
    ignition: float = 0.0                       # 0..100 — готовность импульса
    early_direction: str = "FLAT"              # LONG | SHORT | FLAT (направление-подсказка)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DerivativesSnapshot:
    funding_rate: float | None = None
    funding_trend: str = "unknown"              # rising | falling | overheated_long | overheated_short | neutral
    funding_history: list[float] = field(default_factory=list)
    open_interest_usd: float | None = None
    oi_change_24h_pct: float | None = None
    liq_buy_usd: float = 0.0
    liq_sell_usd: float = 0.0
    liq_imbalance: float = 0.0                  # -1 .. +1 ; + = shorts squeezed
    liq_count: int = 0
    taker_buy_sell_ratio: float | None = None   # legacy alias account ratio (не taker!)
    long_short_ratio: float | None = None       # 0..1 доля длинных счетов
    account_long_ratio: float | None = None     # честное имя: доля длинных счетов Bybit
    mark_price: float | None = None
    index_price: float | None = None
    # ── раунд 4: positioning-матрица OI × funding × цена ─────────
    positioning: str = "unknown"                # healthy_long | overheated_long | short_build | capitulation | short_squeeze | unwinding | building
    positioning_score: float = 50.0             # 0..100
    liq_accel_usd: float = 0.0                  # ликвидации за последние ~5 мин
    score: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrderFlowSnapshot:
    spread_pct: float | None = None
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    imbalance: float = 0.0                      # -1 .. +1
    biggest_bid_wall_usd: float = 0.0
    biggest_ask_wall_usd: float = 0.0
    liquidity_grade: str = "empty"              # excellent | ok | thin | empty
    slippage_pct: float | None = None
    cvd_trend: float = 0.0
    volume_imbalance: float = 0.0               # from CVD proxy
    score: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketContext:
    btc_trend: str = "flat"
    btc_adx: float = 0.0
    btc_volatility: float = 0.0
    btc_score: float = 50.0
    eth_trend: str = "flat"
    eth_24h_pct: float | None = None
    eth_funding_rate: float | None = None
    eth_score: float = 50.0
    dominance: float | None = None
    global_change_pct: float | None = None
    sentiment: float | None = None
    degraded: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RegimeSnapshot:
    regime: MarketRegime = "UNCERTAIN"
    direction: str = "flat"
    volatility_state: str = "normal"
    strength: float = 0.0
    trend_alignment: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    confidence: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FactorScore:
    name: str
    raw: float
    max: float
    weight: float
    value: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreBreakdown:
    total: float = 0.0
    factors: list[FactorScore] = field(default_factory=list)
    penalties: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 1),
            "factors": [f.to_dict() for f in self.factors],
            "penalties": {k: round(v, 2) for k, v in self.penalties.items()},
            "notes": self.notes,
        }


@dataclass
class TradeLevels:
    direction: Literal["LONG", "SHORT", "WAIT"]
    entry_zone: tuple[float, float]
    stop_loss: float
    targets: list[float]
    rr: float
    atr: float
    atr_pct: float
    stop_pct: float
    invalidation: str
    why: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "entry_zone": list(self.entry_zone),
            "stop_loss": self.stop_loss,
            "targets": self.targets,
            "rr": round(self.rr, 2),
            "atr": self.atr,
            "atr_pct": round(self.atr_pct, 3),
            "stop_pct": round(self.stop_pct, 3),
            "invalidation": self.invalidation,
            "why": self.why,
        }


@dataclass
class RiskBrief:
    risk_score: int = 5                         # 1..10
    risk_usd: float = 0.0
    position_pct: float = 0.0
    position_usd: float = 0.0
    qty: float = 0.0
    margin_usd: float = 0.0
    leverage: int = 1
    liquidation_price: float | None = None
    warnings: list[str] = field(default_factory=list)
    max_deposit_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TradingSignal:
    uid: str
    symbol: str
    ts_ms: int
    direction: Direction
    status: SignalStatus = "GENERATED"
    entry_zone: tuple[float, float] = (0.0, 0.0)
    stop_loss: float = 0.0
    targets: list[float] = field(default_factory=list)
    rr: float = 0.0
    tier: Tier = "NONE"
    score: float = 0.0
    confidence: float = 0.0                     # data completeness 0..1
    quality: float = 0.0                        # signal quality 0..100
    regime: MarketRegime = "UNCERTAIN"
    risk_score: int = 5
    leverage: int = 1
    price: float = 0.0
    market: str = "USDT perpetual"
    timeframe: str = "15m"
    horizon: str = "15m-4h"
    source: str = ""                            # реальная биржа-источник (bybit/binance/mexc)
    scenario: str = ""                          # trend | reversal_choch | liquidity_sweep | range_reversion | breakout_watch
    condition: str = ""                         # «условный сетап»: вход при выполнении условия
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    invalidation: str = ""
    no_trade_reasons: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)
    score_breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    risk_brief: RiskBrief = field(default_factory=RiskBrief)
    data_age_seconds: float | None = None
    stale: bool = False
    created_ms: int = field(default_factory=now_ms)
    updated_ms: int = field(default_factory=now_ms)
    duration_sec: float = 0.0

    @property
    def is_signal(self) -> bool:
        return self.direction in ("LONG", "SHORT") and self.status not in ("NO_TRADE", "INVALIDATED", "EXPIRED", "STOPPED")

    @property
    def entry_price(self) -> float:
        return (self.entry_zone[0] + self.entry_zone[1]) / 2 if self.entry_zone else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "symbol": self.symbol,
            "ts_ms": self.ts_ms,
            "direction": self.direction,
            "status": self.status,
            "entry_zone": list(self.entry_zone),
            "stop_loss": self.stop_loss,
            "targets": self.targets,
            "rr": round(self.rr, 2),
            "tier": self.tier,
            "score": round(self.score, 1),
            "confidence": round(self.confidence, 2),
            "quality": round(self.quality, 1),
            "regime": self.regime,
            "risk_score": self.risk_score,
            "leverage": self.leverage,
            "price": self.price,
            "market": self.market,
            "timeframe": self.timeframe,
            "horizon": self.horizon,
            "source": self.source,
            "scenario": self.scenario,
            "condition": self.condition,
            "reasons": self.reasons,
            "risks": self.risks,
            "invalidation": self.invalidation,
            "no_trade_reasons": self.no_trade_reasons,
            "features": self.features,
            "score_breakdown": self.score_breakdown.to_dict(),
            "risk_brief": self.risk_brief.to_dict(),
            "data_age_seconds": self.data_age_seconds,
            "stale": self.stale,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            "duration_sec": round(self.duration_sec, 2),
        }


@dataclass
class ScanCandidate:
    symbol: str
    price: float
    price_24h_pct: float
    turnover_24h: float
    volume_24h: float
    funding_rate: float | None
    open_interest_usd: float | None
    spread_pct: float | None
    high_24h: float = 0.0
    low_24h: float = 0.0
    heat: float = 0.0
    liquidity_ok: bool = False
    volume_ok: bool = False
    reason: str = ""
    # ── раунд 4: ранний отбор (появляется у «намечающихся» движений) ──
    dpos: float = 0.5                       # позиция цены в 24h-диапазоне (0..1)
    rs24: float = 0.0                       # 24h движение относительно BTC
    oi_delta_pct: float | None = None       # изменение OI за 24h (если есть)
    phase: str = "NEUTRAL"                 # EARLY | TRIGGERED | EXHAUSTED | NEUTRAL
    readiness: float = 0.0                  # готовность импульса 0..100
    room_pct: float = 0.0                   # запас до границы диапазона в направлении
    ignition: float = 0.0                   # «подогрев» из emergence (0..100)
    early_direction: str = "FLAT"           # LONG | SHORT | FLAT (подсказка, не сигнал)
    emergence_note: str = ""
    age_days: float | None = None           # возраст листинга
    fresh_listing: bool = False             # листинг младше порога (отдельный режим)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
