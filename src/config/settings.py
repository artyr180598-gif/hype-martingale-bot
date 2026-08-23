"""
Application Configuration and Environment Settings.
"""
import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.config.constants import (
    DEFAULT_SCORE_WEIGHTS,
    DEFAULT_TRACKED_SYMBOLS,
    ExchangeId,
    RiskProfile,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── APPLICATION ─────────────────────────────────────────────
    APP_NAME: str = "CryptoFuturesQuantPlatform"
    APP_VERSION: str = "4.0.0"
    ENVIRONMENT: str = Field(default="development")  # development, staging, production
    DEBUG: bool = Field(default=False)
    LOG_LEVEL: str = Field(default="INFO")
    JSON_LOGS: bool = Field(default=False)

    # ── SECURITY & EXECUTION GATES ──────────────────────────────
    # Live trading is STRICTLY disabled by default
    ENABLE_LIVE_TRADING: bool = Field(
        default=False,
        description="Master safety switch. Must be false for intelligence/paper mode.",
    )
    SECRET_KEY: str = Field(
        default="quant-platform-super-secret-key-change-in-production-32-chars",
        description="Key used for cryptographic token signing",
    )

    # ── DATABASE & STORAGE ──────────────────────────────────────
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./data/quant_platform.db",
        description="Async database connection string. Supports SQLite and PostgreSQL.",
    )
    DB_ECHO: bool = Field(default=False)
    DB_POOL_SIZE: int = Field(default=10)
    DB_MAX_OVERFLOW: int = Field(default=20)

    # ── REDIS CACHE ─────────────────────────────────────────────
    REDIS_URL: str | None = Field(
        default=None,
        description="Optional Redis URL: redis://localhost:6379/0",
    )
    REDIS_REST_URL: str | None = Field(
        default=None,
        description="Optional Upstash REST URL for serverless Redis",
    )
    REDIS_REST_TOKEN: str | None = Field(default=None)

    # ── EXCHANGE API CREDENTIALS ────────────────────────────────
    DEFAULT_EXCHANGE: ExchangeId = Field(default=ExchangeId.BINANCE)

    BINANCE_API_KEY: str = Field(default="")
    BINANCE_API_SECRET: str = Field(default="")
    BINANCE_TESTNET: bool = Field(default=False)

    BYBIT_API_KEY: str = Field(default="")
    BYBIT_API_SECRET: str = Field(default="")
    BYBIT_TESTNET: bool = Field(default=False)

    # Rate limiting & resilience
    EXCHANGE_RATE_LIMIT_CALLS_PER_MIN: int = Field(default=1200)
    EXCHANGE_TIMEOUT_SECONDS: float = Field(default=10.0)
    EXCHANGE_MAX_RETRIES: int = Field(default=3)
    EXCHANGE_BACKOFF_FACTOR: float = Field(default=1.5)

    # ── TELEGRAM BOT ────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = Field(default="")
    TELEGRAM_ADMIN_CHAT_ID: str | None = Field(default=None)
    TELEGRAM_WEBHOOK_URL: str | None = Field(default=None)
    TELEGRAM_USE_POLLING: bool = Field(default=True)
    TELEGRAM_ALERT_MIN_SCORE: float = Field(default=75.0)

    # ── EXTERNAL NEWS & SENTIMENT APIS ──────────────────────────
    CRYPTOPANIC_API_KEY: str = Field(default="")
    CRYPTOCOMPARE_API_KEY: str = Field(default="")
    NEWSAPI_KEY: str = Field(default="")

    # ── QUANTITATIVE ENGINE PARAMETERS ──────────────────────────
    TRACKED_SYMBOLS: list[str] = Field(default_factory=lambda: list(DEFAULT_TRACKED_SYMBOLS))
    DEFAULT_TIMEFRAME: str = "15m"
    MACRO_TIMEFRAME: str = "4h"
    MEDIUM_TIMEFRAME: str = "1h"
    ENTRY_TIMEFRAME: str = "15m"

    # Strategy Score Weights
    SCORE_WEIGHTS: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_SCORE_WEIGHTS)
    )

    # Signal Thresholds
    TIER_EXTREME_THRESHOLD: float = 90.0
    TIER_STRONG_THRESHOLD: float = 80.0
    TIER_VALID_THRESHOLD: float = 70.0
    TIER_WATCH_THRESHOLD: float = 60.0

    # ── RISK ENGINE SETTINGS ────────────────────────────────────
    DEFAULT_RISK_PROFILE: RiskProfile = RiskProfile.BALANCED
    MAX_RISK_PER_TRADE_PERCENT: float = Field(
        default=1.5,
        description="Max % of equity risked per trade based on SL distance",
    )
    MAX_PORTFOLIO_RISK_PERCENT: float = Field(
        default=6.0,
        description="Max total risk across all open positions",
    )
    MAX_CONCURRENT_POSITIONS: int = Field(default=5)
    MAX_LEVERAGE_CEILING: int = Field(default=10)
    DEFAULT_ACCOUNT_EQUITY: float = Field(default=10000.0)

    # Default Execution & Cost Assumptions
    MAKER_FEE_PERCENT: float = 0.02
    TAKER_FEE_PERCENT: float = 0.05
    DEFAULT_SLIPPAGE_PERCENT: float = 0.05
    MAINTENANCE_MARGIN_RATE: float = 0.005  # 0.5% standard for USDT-M

    # ── DATA STORAGE PATHS ──────────────────────────────────────
    DATA_DIRECTORY: str = Field(default="./data")
    HISTORICAL_DATA_DIR: str = Field(default="./data/historical")
    REPORTS_DIR: str = Field(default="./data/reports")


# Singleton instance
settings = Settings()

# Ensure required directories exist
os.makedirs(settings.DATA_DIRECTORY, exist_ok=True)
os.makedirs(settings.HISTORICAL_DATA_DIR, exist_ok=True)
os.makedirs(settings.REPORTS_DIR, exist_ok=True)
