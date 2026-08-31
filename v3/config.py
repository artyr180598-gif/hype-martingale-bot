"""Production configuration for the v3 USDT-perp futures signal engine.

All meaningful thresholds are configurable via env / .env.  Nothing here is
hard-coded into the analysis modules -- the engine receives an immutable
``SignalConfig`` snapshot and uses it everywhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _data_dir_default() -> Path:
    return Path(__import__("os").getenv("DATA_DIR", "./data"))


class SignalConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "v3/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ─────────────────────────────────────────────────────
    APP_NAME: str = "FuturesSignalIntelligence"
    APP_VERSION: str = "3.0.0"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    # ── Exchange / data mode (reuses v1 settings aliases) ────────
    MARKET_DATA_MODE: Literal["auto", "live", "demo"] = "auto"
    BYBIT_API_KEY: str = ""
    BYBIT_API_SECRET: str = ""
    BYBIT_TESTNET: bool = False
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    MEXC_API_KEY: str = ""
    MEXC_API_SECRET: str = ""
    HTTP_TIMEOUT_SECONDS: float = 12.0
    HTTP_MAX_RETRIES: int = 3

    # ── Universe / scanner ──────────────────────────────────────
    SCAN_INTERVAL_SECONDS: int = 600
    SCAN_TOP: int = 20
    SCAN_LIMIT: int = 250
    SCAN_MIN_TURNOVER_USD: float = 20_000_000.0
    SCAN_MIN_VOLUME_USD: float = 5_000_000.0
    WATCHLIST_SYMBOLS: str = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,LINKUSDT,AVAXUSDT,SUIUSDT,TIAUSDT"

    # ── Timeframes ──────────────────────────────────────────────
    TIMEFRAMES: str = "1m,5m,15m,1h,4h"          # order matters: fast -> slow
    ENTRY_TF: str = "15m"
    INTERMEDIATE_TF: str = "1h"
    MACRO_TF: str = "4h"
    ANALYSIS_BARS: int = 400
    MIN_BARS: int = 60

    # ── Data freshness / quality ────────────────────────────────
    MAX_DATA_AGE_SECONDS: float = 90.0
    MAX_SPREAD_PCT: float = 0.35
    MIN_SPREAD_PCT: float = 0.005               # absurd quote -> suspicious
    MIN_BID_ASK_LEVELS: int = 5
    MIN_ORDERBOOK_DEPTH_USD: float = 250_000.0

    # ── Technical / scoring ─────────────────────────────────────
    ADX_TREND_MIN: float = 22.0
    ATR_PCT_NORMAL_MIN: float = 0.3
    ATR_PCT_HIGH: float = 3.5
    ATR_PCT_EXTREME: float = 7.0
    VOLUME_Z_BULL: float = 1.0
    VOLUME_Z_BEAR: float = -1.0
    RSI_OVERBOUGHT: float = 72.0
    RSI_OVERSOLD: float = 28.0
    FUNDING_OVERHEATED: float = 0.002           # 0.2% per funding interval
    FUNDING_OVERBURDENED_SHORT: float = -0.001

    # ── Entry / SL / TP ─────────────────────────────────────────
    ATR_SL_MULTIPLIER: float = 1.8
    ATR_MIN_SL_MULTIPLIER: float = 0.8
    ATR_MAX_SL_MULTIPLIER: float = 3.5
    ATR_TP_MULTIPLIER: float = 3.6
    MIN_RISK_REWARD: float = 1.8
    MAX_ENTRY_DISTANCE_ATR: float = 1.0
    TP_CLOSE_PCT: tuple[float, float, float] = (0.5, 0.3, 0.2)

    # ── Risk ────────────────────────────────────────────────────
    RISK_PER_TRADE_PCT: float = 1.0
    MAX_POSITION_PCT: float = 15.0
    MAX_LEVERAGE: int = 10
    MAX_RISK_SCORE_TO_ENTER: int = 6
    MAX_CORRELATED_POSITIONS: int = 3
    BTC_CORRELATION_PENALTY_THRESHOLD: float = 0.75

    # ── Signal validation / No-Trade ────────────────────────────
    CONFIDENCE_MIN: float = 0.45                # data-quality confidence
    QUALITY_MIN: float = 55.0                   # signal quality score
    S_TIER_MIN: float = 82.0
    A_TIER_MIN: float = 72.0
    B_TIER_MIN: float = 62.0
    C_TIER_MIN: float = 50.0
    SHOW_TIER_MIN: float = 72.0                 # A and above shown by default
    COOLDOWN_SECONDS: int = 3600                # one alert per symbol/hour
    MAX_ACTIVE_SIGNALS: int = 12

    # ── Storage ─────────────────────────────────────────────────
    DATA_DIR: Path = Field(default_factory=_data_dir_default)
    DB_PATH: Path | None = None

    # ── Report ──────────────────────────────────────────────────
    BEGINNER_MODE_DEFAULT: bool = True
    DEFAULT_DEPOSIT_USD: float = 1_000.0
    DEFAULT_EXCHANGE: str = "bybit"

    # ── Telegram ────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN")
    )
    TELEGRAM_ADMIN_CHAT_ID: str = Field(
        default="", validation_alias=AliasChoices("TELEGRAM_ADMIN_CHAT_ID", "TELEGRAM_CHAT_ID")
    )

    # ── AI explanation layer (optional, used only for annotations) ─
    AI_ENABLED: bool = True
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT_SECONDS: float = 20.0

    @field_validator("TIMEFRAMES", mode="before")
    @classmethod
    def _empty_tf(cls, v: object) -> str:
        return v or "1m,5m,15m,1h,4h"

    @field_validator(
        "ATR_SL_MULTIPLIER",
        "ATR_TP_MULTIPLIER",
        "MIN_RISK_REWARD",
        "RISK_PER_TRADE_PCT",
    )
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("value must be > 0")
        return v

    @property
    def timeframes(self) -> list[str]:
        return [t.strip() for t in self.TIMEFRAMES.split(",") if t.strip()]

    @property
    def watchlist(self) -> list[str]:
        out: list[str] = []
        for s in self.WATCHLIST_SYMBOLS.split(","):
            s = s.strip().upper()
            if s and s not in out:
                out.append(s)
        return out

    @property
    def db_path(self) -> Path:
        if self.DB_PATH:
            return Path(self.DB_PATH)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        return self.DATA_DIR / "signals_v3.db"


def load_config(**overrides: object) -> SignalConfig:
    cfg = SignalConfig()
    for key, value in overrides.items():
        if value is not None:
            setattr(cfg, key, value)
    return cfg
