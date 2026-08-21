from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets are loaded from the environment only."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/crypto_intelligence"
    redis_url: str = "redis://redis:6379/0"

    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    bybit_api_key: str | None = None
    bybit_api_secret: str | None = None
    telegram_bot_token: str | None = None

    enable_live_trading: bool = False
    default_timezone: str = "UTC"
    default_min_signal_score: int = Field(default=70, ge=0, le=100)
    max_risk_per_trade: float = Field(default=0.01, gt=0, le=0.10)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
