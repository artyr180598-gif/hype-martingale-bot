"""
Настройки приложения (pydantic-settings, .env / переменные окружения).
"""

import os
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _data_dir_default() -> Path:
    return Path(os.getenv("DATA_DIR", "./data"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── ПРИЛОЖЕНИЕ ─────────────────────────────────────────────
    APP_NAME: str = "HYPE Advisor"
    APP_VERSION: str = "5.0.0"
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "UTC"

    # ── TELEGRAM ───────────────────────────────────────────────
    # Поддерживаем оба варианта имён переменных (старый бот использовал
    # TELEGRAM_TOKEN / TELEGRAM_CHAT_ID, на Railway они уже заданы).
    TELEGRAM_BOT_TOKEN: str = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN")
    )
    TELEGRAM_ADMIN_CHAT_ID: str = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_ADMIN_CHAT_ID", "TELEGRAM_CHAT_ID")
    )
    TELEGRAM_ALERT_MIN_SCORE: float = 70.0

    # ── БИРЖИ / ДАННЫЕ ─────────────────────────────────────────
    MARKET_DATA_MODE: str = "auto"  # auto | live | demo
    BYBIT_API_KEY: str = ""
    BYBIT_API_SECRET: str = ""
    BYBIT_TESTNET: bool = False
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    MEXC_API_KEY: str = ""
    MEXC_API_SECRET: str = ""
    COINGECKO_API_KEY: str = ""
    HTTP_TIMEOUT_SECONDS: float = 12.0
    HTTP_MAX_RETRIES: int = 3

    # ── НАБЛЮДЕНИЕ / СКАНИРОВАНИЕ ──────────────────────────────
    WATCHLIST_SYMBOLS: str = (
        "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,LINKUSDT,AVAXUSDT,SUIUSDT,TIAUSDT"
    )
    WATCH_INTERVAL_SECONDS: int = 600
    SCAN_INTERVAL_SECONDS: int = 1800
    DEEP_ANALYZE_TOP: int = 12          # сколько кандидатов анализировать глубоко за скан
    WATCH_MAX_SYMBOLS: int = 30         # максимум монет в активном наблюдении
    GEM_PROMOTE_MIN_SCORE: float = 66.0  # рейтинг для автодобавления в наблюдение

    # ── ПОРОГИ СОВЕТНИКА ───────────────────────────────────────
    ALERT_MIN_SCORE: float = 70.0
    CHART_MIN_SCORE: float = 60.0
    GEM_MIN_VOLUME_USD: float = 20_000_000.0
    GEM_MIN_SCORE: float = 62.0

    # ── РИСК-ПАРАМЕТРЫ СОВЕТОВ ────────────────────────────────
    RISK_PER_TRADE_PCT: float = 1.0
    MAX_LEVERAGE: int = 10
    MIN_RISK_REWARD: float = 1.8
    MAX_POSITION_PCT: float = 15.0

    # ── ХРАНЕНИЕ ──────────────────────────────────────────────
    DATA_DIR: Path = Field(default_factory=_data_dir_default)
    DB_PATH: Path | None = None
    CHART_DIR: Path | None = None

    # ── WEB-ДАШБОРД ───────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    @property
    def db_path(self) -> Path:
        if self.DB_PATH:
            return Path(self.DB_PATH)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        return self.DATA_DIR / "advisor.db"

    @property
    def chart_dir(self) -> Path:
        if self.CHART_DIR:
            p = Path(self.CHART_DIR)
        else:
            p = self.DATA_DIR / "charts"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def watchlist(self) -> list[str]:
        syms = [s.strip().upper() for s in self.WATCHLIST_SYMBOLS.split(",") if s.strip()]
        seen: list[str] = []
        for s in syms:
            if s not in seen:
                seen.append(s)
        return seen

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN)


settings = Settings()
