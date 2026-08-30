"""
Конфигурация v2 — всё включается/выключается через .env.

Принципы:
  * каждый фильтр сканера имеет флаг ``*_ENABLED`` и числовой порог;
  * отсутствие ключа API никогда не является ошибкой — модуль уходит в
    заглушку и честно помечает данные как ``is_stub=True``;
  * значения валидируются на старте (pydantic), а не в момент сделки.

Имена переменных совпадают с именами полей (без префикса), поэтому в .env
пишется ровно то, что видно здесь: SCAN_L1_ENABLED=false и т.д.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_files() -> tuple[str, ...]:
    """Ищем .env в корне проекта и в каталоге v2/ (локальные переопределения)."""
    here = Path(__file__).resolve().parent
    return (str(here.parent / ".env"), str(here / ".env"))


class V2Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ══ ПРИЛОЖЕНИЕ / ЛОГИ ════════════════════════════════════════
    APP_NAME: str = "HYPE Advisor v2"
    APP_VERSION: str = "2.0.0"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    # ══ РЕЖИМ ДАННЫХ ═════════════════════════════════════════════
    # auto — живые провайдеры, при отказе деградируем в demo;
    # live — только живые (ошибка, если ничего не отвечает);
    # demo — полностью синтетический рынок (офлайн, тесты, CI).
    DATA_MODE: Literal["auto", "live", "demo"] = "auto"
    HTTP_TIMEOUT_SECONDS: float = 10.0
    HTTP_MAX_RETRIES: int = 3
    HTTP_BACKOFF_BASE: float = 0.5
    HTTP_CONCURRENCY: int = 8              # глобальный лимит одновременных запросов
    REQUESTS_PER_SECOND: float = 6.0       # token-bucket на хост
    CIRCUIT_FAILURE_THRESHOLD: int = 5     # сколько ошибок до открытия предохранителя
    CIRCUIT_COOLDOWN_SECONDS: float = 60.0

    # ══ WEBSOCKET ════════════════════════════════════════════════
    USE_WEBSOCKET: bool = True
    WS_PING_INTERVAL_SECONDS: float = 20.0
    WS_RECONNECT_BASE_SECONDS: float = 1.0
    WS_RECONNECT_MAX_SECONDS: float = 60.0
    WS_STALE_SECONDS: float = 45.0         # тишина дольше → принудительный реконнект

    # ══ УРОВЕНЬ 1: БЫСТРЫЙ СКАНЕР ════════════════════════════════
    SCAN_L1_ENABLED: bool = True
    L1_MIN_VOLUME_5M_USD: float = 500_000.0     # оборот за 5 минут
    L1_MIN_TX_5M: int = 100                     # число транзакций (buys + sells)
    L1_MIN_LIQUIDITY_USD: float = 50_000.0      # ликвидность в пуле (мусор отсекаем сразу)
    L1_MAX_SPREAD_PCT: float = 5.0              # спред шире — не торговать
    L1_MIN_PAIR_AGE_HOURS: float = 1.0          # совсем свежие пулы слишком шумные
    L1_MAX_PAIR_AGE_HOURS: float = 24 * 365 * 5
    L1_QUOTE_WHITELIST: str = "USDC,USDT,WETH,WSOL,WBNB,BUSD,DAI"
    L1_MAX_CANDIDATES: int = 60                 # сколько пропустить на уровень 2
    L1_BLOCKLIST_SYMBOLS: str = "USDC,USDT,DAI,BUSD,USD1,WETH,WSOL,WBNB,WBTC"

    # ══ УРОВЕНЬ 2: ГЛУБОКИЙ (СКАМ-ФИЛЬТР) ════════════════════════
    SCAN_L2_ENABLED: bool = True
    L2_CHECK_HOLDERS: bool = True
    L2_MAX_TOP10_PCT: float = 40.0              # концентрация топ-10 > 40% → блок
    L2_MAX_TOP1_PCT: float = 25.0               # один кит > 25% → блок
    L2_MIN_HOLDERS: int = 200
    L2_CHECK_LP_LOCK: bool = True
    L2_MIN_LP_LOCKED_PCT: float = 80.0          # заблокировано ≥ 80% LP
    L2_MIN_LP_LOCK_DAYS: int = 180              # ≥ 6 месяцев
    L2_CHECK_CONTRACT: bool = True
    L2_BLOCK_IF_MINTABLE: bool = True           # есть mint() → блок
    L2_BLOCK_IF_BLACKLIST: bool = True          # есть blacklist() → блок
    L2_BLOCK_IF_HONEYPOT: bool = True
    L2_MAX_BUY_TAX_PCT: float = 10.0
    L2_MAX_SELL_TAX_PCT: float = 10.0
    L2_REQUIRE_VERIFIED_SOURCE: bool = False    # строгий режим: только верифицированные
    L2_MIN_LIQ_TO_MCAP: float = 0.03            # ликвидность/капа ≥ 3%
    L2_MAX_LIQ_TO_MCAP: float = 0.95            # >95% — «пустышка без рынка»
    L2_CONCURRENCY: int = 6

    # ══ УРОВЕНЬ 3: ОНЧЕЙН ════════════════════════════════════════
    SCAN_L3_ENABLED: bool = True
    L3_MIN_DEPLOYER_AGE_DAYS: int = 7
    L3_MAX_DEPLOYER_TOKENS: int = 25            # серийный деплоер скамов
    L3_MAX_DEPLOYER_FUNDED_AGE_HOURS: int = 48  # кошелёк создан и сразу деплоит
    L3_MIN_DEPLOYER_TX_COUNT: int = 5           # «пустой» деплоер подозрителен
    L3_BLOCK_IF_DEPLOYER_SOLD: bool = True      # деплоер слил весь свой стейк
    L3_CONCURRENCY: int = 4

    # ══ СКАНЕР: ОБЩЕЕ ════════════════════════════════════════════
    SCAN_INTERVAL_SECONDS: int = 300
    SCAN_TOP_RESULTS: int = 20
    SCAN_CACHE_TTL_SECONDS: float = 45.0
    SCAN_MIN_FINAL_SCORE: float = 45.0          # ниже — не показываем как «находку»

    # ══ АНАЛИЗ ПО ЗАПРОСУ ════════════════════════════════════════
    ANALYSIS_TREND_TF: str = "1h"               # ADX-тренд
    ANALYSIS_ACCUM_TF: str = "15m"              # OBV-накопление
    ANALYSIS_BARS: int = 300
    ANALYSIS_ENTRY_SIZE_USD: float = 5_000.0    # для оценки проскальзывания
    ANALYSIS_SOCIAL_WINDOW_HOURS: int = 2
    ANALYSIS_ORDERBOOK_DEPTH: int = 50

    # ══ РИСК-МЕНЕДЖЕР (динамические уровни от ATR) ═══════════════
    ATR_PERIOD: int = 14
    ATR_SL_MULTIPLIER: float = 1.8              # стоп = вход ∓ 1.8·ATR
    ATR_TP_MULTIPLIER: float = 3.6              # цель  = вход ± 3.6·ATR (R:R 1:2)
    ATR_MAX_SL_MULTIPLIER: float = 3.5          # стоп не дальше 3.5·ATR
    ATR_MIN_SL_MULTIPLIER: float = 0.8          # и не ближе 0.8·ATR (иначе шум выбьет)
    MIN_RISK_REWARD: float = 2.0                # жёсткое требование 1:2
    RISK_PER_TRADE_PCT: float = 1.0             # риск на сделку, % депозита
    MAX_POSITION_PCT: float = 10.0              # потолок позиции, % депозита
    MAX_LEVERAGE: int = 5
    MAX_RISK_SCORE_TO_ENTER: int = 6            # риск ≥ 7 → «Не входить»
    TRAILING_ATR_MULTIPLIER: float = 2.5        # трейлинг-стоп за 2.5·ATR
    DEFAULT_DEPOSIT_USD: float = 1_000.0
    TAKER_FEE_PCT: float = 0.06                 # 0.06% на сторону (DEX-своп + газ)

    # ══ ИСПОЛНЕНИЕ ОРДЕРОВ ═══════════════════════════════════════
    # paper — журнал виртуальных сделок (по умолчанию, безопасно);
    # dry_run — всё считается, ордер не отправляется и не пишется;
    # live — реальная отправка (нужны ключи + EXECUTOR_ALLOW_LIVE=true).
    EXECUTOR_MODE: Literal["paper", "dry_run", "live"] = "paper"
    EXECUTOR_ALLOW_LIVE: bool = False
    EXECUTOR_JOURNAL_PATH: Path = Path("./data/v2_orders.jsonl")

    # ══ ВНЕШНИЕ КЛЮЧИ (все опциональны) ══════════════════════════
    DEXSCREENER_ENABLED: bool = True
    GECKOTERMINAL_ENABLED: bool = True
    GOPLUS_ENABLED: bool = True
    RUGDOC_ENABLED: bool = False
    ETHERSCAN_API_KEY: str = ""
    BSCSCAN_API_KEY: str = Field(
        default="", validation_alias=AliasChoices("BSCSCAN_API_KEY", "BSC_SCAN_API_KEY")
    )
    SOLSCAN_API_KEY: str = ""
    MORALIS_API_KEY: str = ""
    X_BEARER_TOKEN: str = Field(
        default="", validation_alias=AliasChoices("X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN")
    )
    LUNACRUSH_API_KEY: str = ""

    # ══ AI (OpenAI-совместимый) ══════════════════════════════════
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    AI_ENABLED: bool = True
    AI_CONTRACT_ANALYSIS: bool = True           # разбор байткода/исходников
    AI_SOCIAL_SCREENING: bool = True            # оценка хайпа по твитам
    AI_TIMEOUT_SECONDS: float = 20.0
    AI_MAX_TOKENS: int = 700

    # ══ CEX (для стаканов/свечей листингованных токенов) ══════════
    CEX_ENABLED: bool = True
    BYBIT_API_KEY: str = ""
    BYBIT_API_SECRET: str = ""
    BYBIT_TESTNET: bool = False
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""

    # ══ TELEGRAM / API ═══════════════════════════════════════════
    TELEGRAM_BOT_TOKEN: str = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN")
    )
    TELEGRAM_ADMIN_CHAT_ID: str = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_ADMIN_CHAT_ID", "TELEGRAM_CHAT_ID")
    )
    HOST: str = "0.0.0.0"
    PORT: int = 8100

    # ── валидаторы ───────────────────────────────────────────────
    @field_validator("L1_QUOTE_WHITELIST", "L1_BLOCKLIST_SYMBOLS", mode="before")
    @classmethod
    def _strip(cls, v):
        return v or ""

    @field_validator("ATR_SL_MULTIPLIER", "ATR_TP_MULTIPLIER", "MIN_RISK_REWARD", "RISK_PER_TRADE_PCT")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("значение должно быть больше нуля")
        return v

    # ── вычисляемые свойства ─────────────────────────────────────
    @property
    def quote_whitelist(self) -> set[str]:
        return {s.strip().upper() for s in self.L1_QUOTE_WHITELIST.split(",") if s.strip()}

    @property
    def blocklist_symbols(self) -> set[str]:
        return {s.strip().upper() for s in self.L1_BLOCKLIST_SYMBOLS.split(",") if s.strip()}

    @property
    def ai_available(self) -> bool:
        return self.AI_ENABLED and bool(self.OPENAI_API_KEY)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN)

    def filters_summary(self) -> dict[str, bool]:
        """Какие фильтры сейчас активны — выводится в шапке отчёта скана."""
        return {
            "L1 quick": self.SCAN_L1_ENABLED,
            "L2 scam": self.SCAN_L2_ENABLED,
            "L3 onchain": self.SCAN_L3_ENABLED,
            "holders": self.SCAN_L2_ENABLED and self.L2_CHECK_HOLDERS,
            "lp_lock": self.SCAN_L2_ENABLED and self.L2_CHECK_LP_LOCK,
            "contract": self.SCAN_L2_ENABLED and self.L2_CHECK_CONTRACT,
            "ai": self.ai_available,
            "websocket": self.USE_WEBSOCKET,
        }


def load_config(**overrides) -> V2Config:
    """Загрузить конфиг; overrides нужны тестам и CLI-флагам."""
    cfg = V2Config()
    for key, value in overrides.items():
        if value is not None:
            setattr(cfg, key, value)
    return cfg


config = V2Config()
