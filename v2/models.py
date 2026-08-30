"""
Модели данных v2.

Все сущности — dataclass'ы с ``to_dict()``: их одинаково удобно отдавать в
JSON-API, писать в журнал сделок и рендерить в Markdown-отчёт.

Важное соглашение: любое поле, которое не удалось получить у провайдера,
остаётся ``None`` (а не 0.0). Ноль и «нет данных» — принципиально разные вещи:
«ликвидность 0» блокирует токен, а «ликвидность неизвестна» — нет.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Verdict = Literal["ENTER", "WATCH", "AVOID"]

VERDICT_RU: dict[Verdict, str] = {"ENTER": "Входить", "WATCH": "Наблюдать", "AVOID": "Не входить"}


def now_ms() -> int:
    return int(time.time() * 1000)


# ═══════════════════════════════════════════════════════════════
#  РЫНОК
# ═══════════════════════════════════════════════════════════════
@dataclass
class Candle:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TokenCandidate:
    """Токен + его пул. Единица работы сканера."""

    chain: str                       # ethereum | bsc | solana | base ...
    address: str                     # адрес токена (0x... / mint)
    symbol: str
    name: str = ""
    pair_address: str = ""
    dex: str = ""                    # uniswap | raydium | pancakeswap
    quote_symbol: str = "USDC"
    price_usd: float = 0.0
    # активность (окно 5 минут — ключевой фильтр уровня 1)
    volume_5m_usd: float = 0.0
    volume_1h_usd: float = 0.0
    volume_24h_usd: float = 0.0
    tx_5m: int = 0
    buys_5m: int = 0
    sells_5m: int = 0
    tx_1h: int = 0
    tx_24h: int = 0
    # ликвидность / оценка
    liquidity_usd: float = 0.0
    fdv_usd: float = 0.0
    market_cap_usd: float = 0.0
    price_change_5m_pct: float = 0.0
    price_change_1h_pct: float = 0.0
    price_change_24h_pct: float = 0.0
    pair_created_ms: int = 0
    cex_symbol: str = ""             # если токен листингован на CEX (BTCUSDT)
    source: str = ""                 # dexscreener | geckoterminal | demo
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def age_hours(self) -> float:
        if not self.pair_created_ms:
            return 0.0
        return max(0.0, (now_ms() - self.pair_created_ms) / 3_600_000)

    @property
    def market_cap_effective(self) -> float:
        return self.market_cap_usd or self.fdv_usd

    @property
    def liq_to_mcap(self) -> float | None:
        mcap = self.market_cap_effective
        if not mcap or mcap <= 0:
            return None
        return self.liquidity_usd / mcap

    @property
    def buy_ratio_5m(self) -> float:
        total = self.buys_5m + self.sells_5m
        return (self.buys_5m / total) if total else 0.5

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.address.lower()}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrderBookLevel:
    price: float
    qty: float


@dataclass
class OrderBookSnapshot:
    symbol: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    ts_ms: int = 0
    source: str = ""
    is_stub: bool = False

    @property
    def mid(self) -> float:
        if self.bids and self.asks:
            return (self.bids[0].price + self.asks[0].price) / 2
        if self.bids:
            return self.bids[0].price
        if self.asks:
            return self.asks[0].price
        return 0.0

    @property
    def spread_pct(self) -> float:
        if not (self.bids and self.asks) or self.mid <= 0:
            return 0.0
        return (self.asks[0].price - self.bids[0].price) / self.mid * 100


# ═══════════════════════════════════════════════════════════════
#  УРОВЕНЬ 2: БЕЗОПАСНОСТЬ
# ═══════════════════════════════════════════════════════════════
@dataclass
class HolderStats:
    top1_pct: float | None = None
    top10_pct: float | None = None
    holders_count: int | None = None
    deployer_pct: float | None = None
    lp_in_top10: bool | None = None      # LP-контракт внутри топ-10 (норма)
    source: str = ""
    is_stub: bool = False
    checked_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LpLockInfo:
    locked_pct: float | None = None      # доля заблокированной ликвидности
    locked_until_ms: int | None = None   # когда разблокируется
    lock_days_left: float | None = None
    locker: str = ""                     # uniccrypt | team.finance | pinklock ...
    source: str = ""
    is_stub: bool = False
    checked_at_ms: int = field(default_factory=now_ms)

    @property
    def locked_forever(self) -> bool:
        return bool(self.locked_until_ms and self.locked_until_ms > 4_000_000_000_000)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContractRisk:
    """Флаги контракта: mint, blacklist, honeypot, налоги, прокси."""

    is_mintable: bool | None = None
    has_blacklist: bool | None = None
    has_owner: bool | None = None
    owner_can_change_balance: bool | None = None
    is_proxy: bool | None = None
    is_honeypot: bool | None = None
    buy_tax_pct: float | None = None
    sell_tax_pct: float | None = None
    source_verified: bool | None = None
    is_open_source: bool | None = None
    cannot_sell_all: bool | None = None
    cannot_buy: bool | None = None
    owner_address: str = ""
    functions_found: list[str] = field(default_factory=list)
    ai_notes: str = ""
    ai_verdict: str = ""                 # safe | suspicious | dangerous | ""
    source: str = ""
    is_stub: bool = False
    checked_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SecurityReport:
    """Сводный вывод уровня 2 + уровень 3 (ончейн)."""

    score: float = 0.0                   # 0..100, больше = безопаснее
    blocked: bool = False
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    holders: HolderStats = field(default_factory=HolderStats)
    lp: LpLockInfo = field(default_factory=LpLockInfo)
    contract: ContractRisk = field(default_factory=ContractRisk)
    deployer: "DeployerInfo" = field(default_factory=lambda: DeployerInfo())
    degraded: list[str] = field(default_factory=list)   # какие проверки не отработали

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "blocked": self.blocked,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "passed": self.passed,
            "holders": self.holders.to_dict(),
            "lp": self.lp.to_dict(),
            "contract": self.contract.to_dict(),
            "deployer": self.deployer.to_dict(),
            "degraded": self.degraded,
        }


# ═══════════════════════════════════════════════════════════════
#  УРОВЕНЬ 3: ОНЧЕЙН
# ═══════════════════════════════════════════════════════════════
@dataclass
class DeployerInfo:
    address: str = ""
    age_days: float | None = None
    first_tx_ms: int | None = None
    tx_count: int | None = None
    tokens_deployed: int | None = None
    funded_by: str = ""
    funded_by_age_hours: float | None = None
    balance_native: float | None = None
    sold_out: bool | None = None         # деплоер слил весь стейк
    flagged: bool | None = None          # в чёрных списках
    prior_projects: list[str] = field(default_factory=list)
    source: str = ""
    is_stub: bool = False
    checked_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════
#  АНАЛИЗ
# ═══════════════════════════════════════════════════════════════
@dataclass
class MicrostructureReport:
    """Стакан: стены, глубина, проскальзывание для входа заданного объёма."""

    entry_size_usd: float = 0.0
    mid_price: float = 0.0
    spread_pct: float = 0.0
    bid_depth_1pct_usd: float = 0.0
    ask_depth_1pct_usd: float = 0.0
    imbalance: float = 0.0               # -1..+1
    biggest_bid_wall_usd: float = 0.0
    biggest_bid_wall_price: float = 0.0
    biggest_ask_wall_usd: float = 0.0
    biggest_ask_wall_price: float = 0.0
    slippage_pct: float = 0.0            # на entry_size_usd
    est_fill_price: float = 0.0
    slippage_cost_usd: float = 0.0
    grade: str = ""                      # excellent | ok | thin | empty
    notes: list[str] = field(default_factory=list)
    is_stub: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrendSnapshot:
    timeframe: str
    adx: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    rsi: float = 50.0
    atr: float = 0.0
    atr_pct: float = 0.0
    direction: str = "flat"              # up | down | flat
    strength: str = "weak"               # strong | moderate | weak | none
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AccumulationSnapshot:
    timeframe: str
    obv_slope: float = 0.0               # нормированный наклон OBV
    obv_divergence: float = 0.0          # >0 — OBV растёт сильнее цены
    volume_zscore: float = 0.0
    accumulation: bool = False
    distribution: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FibSnapshot:
    swing_low: float = 0.0
    swing_high: float = 0.0
    direction: int = 1
    retracements: dict[str, float] = field(default_factory=dict)
    extensions: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TechnicalReport:
    price: float = 0.0
    trend: TrendSnapshot = field(default_factory=lambda: TrendSnapshot(timeframe="1h"))
    accumulation: AccumulationSnapshot = field(
        default_factory=lambda: AccumulationSnapshot(timeframe="15m")
    )
    fib: FibSnapshot = field(default_factory=FibSnapshot)
    atr: float = 0.0
    atr_pct: float = 0.0
    vwap: float = 0.0
    change_24h_pct: float = 0.0
    score: float = 50.0                  # 0..100
    notes: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "trend": self.trend.to_dict(),
            "accumulation": self.accumulation.to_dict(),
            "fib": self.fib.to_dict(),
            "atr": self.atr,
            "atr_pct": round(self.atr_pct, 3),
            "vwap": self.vwap,
            "change_24h_pct": self.change_24h_pct,
            "score": round(self.score, 1),
            "notes": self.notes,
            "degraded": self.degraded,
        }


@dataclass
class SocialReport:
    window_hours: int = 2
    mentions: int = 0
    unique_authors: int = 0
    hype_score: float = 0.0              # 0..100
    sentiment: float = 0.0               # -1..+1
    top_posts: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    ai_notes: str = ""
    source: str = "stub"
    is_stub: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TradePlan:
    """Конкретные числа для входа — всё считается от ATR."""

    direction: str = "WAIT"              # LONG | SHORT | WAIT
    entry: float = 0.0
    stop_loss: float = 0.0
    targets: list[float] = field(default_factory=list)
    atr: float = 0.0
    atr_sl_pct: float = 0.0              # на сколько % стоп от входа
    rr: float = 0.0
    position_pct: float = 0.0            # % депозита
    position_usd: float = 0.0
    qty: float = 0.0
    risk_usd: float = 0.0
    leverage: int = 1
    trailing_stop: float = 0.0
    invalidation: str = ""
    why: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoinReport:
    """Финальный отчёт по монете — то, что видит пользователь."""

    token: TokenCandidate
    security: SecurityReport = field(default_factory=SecurityReport)
    micro: MicrostructureReport = field(default_factory=MicrostructureReport)
    technical: TechnicalReport = field(default_factory=TechnicalReport)
    social: SocialReport = field(default_factory=SocialReport)
    plan: TradePlan = field(default_factory=TradePlan)
    verdict: Verdict = "WATCH"
    risk_score: int = 5                  # 1..10 (10 — максимум риска)
    confidence: float = 0.0              # 0..1 — насколько данные полные
    score: float = 0.0                   # 0..100 — интегральная привлекательность
    summary: str = ""
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    created_ms: int = field(default_factory=now_ms)
    duration_sec: float = 0.0

    @property
    def verdict_ru(self) -> str:
        return VERDICT_RU[self.verdict]

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token.to_dict(),
            "security": self.security.to_dict(),
            "micro": self.micro.to_dict(),
            "technical": self.technical.to_dict(),
            "social": self.social.to_dict(),
            "plan": self.plan.to_dict(),
            "verdict": self.verdict,
            "verdict_ru": self.verdict_ru,
            "risk_score": self.risk_score,
            "confidence": round(self.confidence, 2),
            "score": round(self.score, 1),
            "summary": self.summary,
            "reasons": self.reasons,
            "risks": self.risks,
            "degraded": self.degraded,
            "created_ms": self.created_ms,
            "duration_sec": round(self.duration_sec, 2),
        }


@dataclass
class ScanStageResult:
    """Результат одного уровня сканера (для отчёта и логов)."""

    level: int
    name: str
    entered: int = 0
    passed: int = 0
    rejected: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    duration_sec: float = 0.0
    degraded: list[str] = field(default_factory=list)

    def note(self, reason: str) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "name": self.name,
            "entered": self.entered,
            "passed": self.passed,
            "rejected": self.rejected,
            "rejections": dict(sorted(self.rejections.items(), key=lambda kv: -kv[1])),
            "duration_sec": round(self.duration_sec, 2),
            "degraded": self.degraded,
        }


@dataclass
class ScanResult:
    stages: list[ScanStageResult] = field(default_factory=list)
    survivors: list[tuple[TokenCandidate, SecurityReport]] = field(default_factory=list)
    reports: list[CoinReport] = field(default_factory=list)
    mode: str = "auto"
    started_ms: int = field(default_factory=now_ms)
    duration_sec: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def total_in(self) -> int:
        return self.stages[0].entered if self.stages else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [s.to_dict() for s in self.stages],
            "survivors": [
                {"token": t.to_dict(), "security": s.to_dict()} for t, s in self.survivors
            ],
            "mode": self.mode,
            "duration_sec": round(self.duration_sec, 2),
            "errors": self.errors,
        }
