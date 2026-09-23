"""Production config for HYPE ULTIMATE — all thresholds via env/.env"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "4.0.0"
APP_RELEASE = "ULTIMATE v4: Multi-exchange + STOBB/SBM/JUMP + Liquidity + Confluence"

DEFAULT_CONFIDENCE_WEIGHTS = {
    "quality": 0.30,
    "data": 0.15,
    "trend": 0.20,
    "confirm": 0.15,
    "risk": 0.10,
    "impulse": 0.10,
}

def build_line(version: str | None = None, release: str | None = None) -> str:
    return f"🛠 Сборка: v{version or APP_VERSION} · {release or APP_RELEASE}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "src/hype/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ──
    APP_NAME: str = "HypeUltimate"
    APP_VERSION: str = APP_VERSION
    APP_RELEASE: str = APP_RELEASE
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False
    V3_API_TOKEN: str = ""  # legacy alias support
    API_TOKEN: str = ""
    HOST: str = "0.0.0.0"
    PORT: int = 8400
    DATA_DIR: Path = Field(default_factory=lambda: Path("./data"))
    DB_PATH: str = ""

    # ── Telegram ──
    TELEGRAM_BOT_TOKEN: str = Field(default="", validation_alias="TELEGRAM_TOKEN")
    TELEGRAM_ADMIN_CHAT_ID: str = Field(default="", validation_alias="TELEGRAM_CHAT_ID")
    TELEGRAM_ALLOWED_USER_IDS: str = ""
    TELEGRAM_CHAT_ID: str = ""  # alias

    # ── Exchanges (public data works without keys) ──
    EXCHANGES: str = "binance,bybit,okx,mexc,kucoin,gate,bitget"
    PRIMARY_EXCHANGE: str = "bybit"
    SECONDARY_EXCHANGES: str = "binance,okx,mexc"
    CCXT_TIMEOUT: int = 15000
    CCXT_ENABLE_RATELIMIT: bool = True

    BYBIT_API_KEY: str = ""
    BYBIT_API_SECRET: str = ""
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    OKX_API_KEY: str = ""
    OKX_API_SECRET: str = ""
    MEXC_API_KEY: str = ""
    MEXC_API_SECRET: str = ""
    KUCOIN_API_KEY: str = ""
    KUCOIN_API_SECRET: str = ""
    GATE_API_KEY: str = ""
    GATE_API_SECRET: str = ""
    BITGET_API_KEY: str = ""
    BITGET_API_SECRET: str = ""

    HTTP_TIMEOUT_SECONDS: float = 12.0
    HTTP_MAX_RETRIES: int = 3

    # ── Market Data Mode ──
    MARKET_DATA_MODE: Literal["live", "auto"] = "live"

    # ── Scanner ──
    SCAN_INTERVAL_SECONDS: int = 600
    SCAN_TOP: int = 25
    SCAN_LIMIT: int = 300
    SCAN_MIN_TURNOVER_USD: float = 15_000_000.0
    SCAN_MIN_VOLUME_USD: float = 5_000_000.0
    SCAN_SHOW_QUALITY_MIN: float = 70.0
    SCAN_LIST_QUALITY_MIN: float = 55.0
    SCAN_EMERGENCE_ENABLED: bool = True
    SCAN_EMERGENCE_POOL: int = 60
    SCAN_EMERGENCE_BARS: int = 120
    SCAN_EMERGENCE_BOOST: float = 0.30
    SCAN_EXCLUDE_EXHAUSTED: bool = True
    SCAN_AGE_DAYS_MIN: int = 7
    WATCHER_SCAN_UNIVERSE: bool = True
    DIVERSITY_MAX_PER_CLUSTER: int = 2
    WATCHLIST_SYMBOLS: str = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,LINKUSDT,AVAXUSDT,SUIUSDT,TIAUSDT,ARBUSDT,APTUSDT,OPUSDT,INJUSDT"

    # ── Timeframes ──
    TIMEFRAMES: str = "5m,15m,1h,4h,1d"
    ENTRY_TF: str = "15m"
    INTERMEDIATE_TF: str = "1h"
    MACRO_TF: str = "4h"
    ANALYSIS_BARS: int = 400
    MIN_BARS: int = 60

    # ── Data freshness ──
    MAX_DATA_AGE_SECONDS: float = 180.0
    TICKER_CACHE_TTL_SECONDS: float = 10.0
    KLINES_CACHE_TTL_SECONDS: float = 15.0
    ORDERBOOK_CACHE_TTL_SECONDS: float = 5.0
    FUNDING_CACHE_TTL_SECONDS: float = 300.0
    LIQUIDATIONS_CACHE_TTL_SECONDS: float = 60.0
    ORDERBOOK_DEPTH: int = 50

    # ── Indicators thresholds ──
    ADX_TREND_MIN: float = 20.0
    ATR_PCT_NORMAL_MIN: float = 0.25
    ATR_PCT_HIGH: float = 4.0
    ATR_PCT_EXTREME: float = 8.0
    VOLUME_Z_BULL: float = 1.0
    VOLUME_Z_BEAR: float = -1.0
    RSI_OVERBOUGHT: float = 72.0
    RSI_OVERSOLD: float = 28.0
    RSI_MID: float = 50.0
    STOCH_OVERBOUGHT: float = 80.0
    STOCH_OVERSOLD: float = 20.0
    FUNDING_OVERHEATED: float = 0.002
    FUNDING_OVERBURDENED_SHORT: float = -0.001

    # STOBB/SBM
    STOBB_STOCH_K_MAX: float = 25.0
    STOBB_STOCH_D_MAX: float = 25.0
    STOBB_BB_POS_MAX: float = 0.15  # close near lower BB
    SBM_EMA_FAST: int = 20
    SBM_EMA_MID: int = 50
    SBM_EMA_SLOW: int = 200
    SBM_PSAR_ENABLED: bool = True

    # JUMP
    JUMP_PRICE_PCT_MIN: float = 3.0
    JUMP_VOLUME_MULT_MIN: float = 2.5
    JUMP_LOOKBACK_BARS: int = 6

    # Liquidity / Orderflow
    ORDERBOOK_IMBALANCE_BULL: float = 0.65
    ORDERBOOK_IMBALANCE_BEAR: float = 0.35
    LIQUIDITY_WALL_USD: float = 100_000.0
    LIQUIDITY_SWEEP_ATR: float = 1.5

    # Emergence / early impulse
    EMERGENCE_RVOL_WINDOW: int = 20
    EMERGENCE_RVOL_MIN: float = 1.4
    EMERGENCE_SQUEEZE_LOOKBACK: int = 8
    EMERGENCE_CONSOLIDATION_BARS: int = 12
    EMERGENCE_CONSOLIDATION_ATR: float = 1.8
    EMERGENCE_COMPRESSION_ATR_RATIO: float = 0.85
    EMERGENCE_BREAKOUT_LOOKBACK: int = 20
    EMERGENCE_MAX_TRIGGER_ATR: float = 0.85
    EMERGENCE_MIN_BREAKOUT_PRESSURE: float = 0.20
    EMERGENCE_MAX_RECENT_MOVE_ATR: float = 2.8
    EMERGENCE_MIN_ROOM_PCT: float = 0.12
    EMERGENCE_IGNITION_MIN: float = 48.0
    CYCLE_DIRECTION_SCORE_MIN: float = 55.0
    CYCLE_BIAS_MARGIN_MIN: float = 10.0
    CYCLE_TRADE_SCORE_MIN: float = 65.0
    CYCLE_TRADING_ENABLED: bool = True

    # Entry / Risk
    ATR_SL_MULTIPLIER: float = 2.2
    ATR_MIN_SL_MULTIPLIER: float = 0.8
    ATR_MAX_SL_MULTIPLIER: float = 3.5
    ATR_STOP_BUFFER: float = 0.25
    ATR_TP_MULTIPLIER: float = 3.6
    MIN_RISK_REWARD: float = 1.8
    MIN_RISK_REWARD_REVERSAL: float = 1.4
    MAX_ENTRY_DISTANCE_ATR: float = 1.2
    ENTRY_MAX_EXTENSION_ATR: float = 2.2
    TP_CLOSE_PCT: tuple[float, float, float] = (0.5, 0.3, 0.2)
    TP1_R: float = 1.0
    TP2_R: float = 2.0
    TP3_R: float = 3.2
    RISK_PER_TRADE_PCT: float = 1.0
    MAX_POSITION_PCT: float = 15.0
    MAX_LEVERAGE: int = 10
    MAX_RISK_SCORE_TO_ENTER: int = 7
    QUALITY_MIN: float = 52.0
    CONFIDENCE_MIN: float = 0.45

    # Bot confidence
    BOT_CONFIDENCE_WEIGHTS: str = "quality:0.30,data:0.15,trend:0.20,confirm:0.15,risk:0.10,impulse:0.10"
    BOT_CONFIDENCE_HIGH_MIN: float = 75.0
    BOT_CONFIDENCE_MEDIUM_MIN: float = 55.0
    BOT_CONFIDENCE_LOW_MIN: float = 35.0

    # Alerts
    ALERTS_ENABLED: bool = True
    WATCHER_INTERVAL_SECONDS: int = 180
    ALERT_MIN_QUALITY: float = 75.0
    ALERT_MIN_BOT_CONFIDENCE: float = 68.0
    ALERT_MIN_DATA_CONFIDENCE: float = 0.55
    ALERT_MAX_RISK_SCORE: int = 6
    ALERT_MIN_RR: float = 1.8
    ALERT_REQUIRE_FRESH: bool = True
    ALERT_MAX_PER_CYCLE: int = 4
    ALERT_STOPOUT_GUARD: int = 2
    ALERT_STOPOUT_PAUSE_HOURS: int = 6
    ALERT_CHAT_IDS: str = ""

    # AI
    AI_ENABLED: bool = True
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT_SECONDS: float = 20.0

    # Liquidity WS
    LIQUIDATIONS_WS_ENABLED: bool = True
    LIQUIDATIONS_WS_MAX_AGE_SECONDS: float = 900.0
    ETH_CONTEXT_ENABLED: bool = True

    @field_validator("MARKET_DATA_MODE", mode="before")
    @classmethod
    def _data_mode_real_only(cls, v: object) -> object:
        value = str(v).strip().lower() if v is not None else ""
        if value == "demo":
            raise ValueError("MARKET_DATA_MODE=demo removed — use live|auto")
        if value and value not in ("live", "auto"):
            raise ValueError(f"Unknown MARKET_DATA_MODE={value!r}: use live|auto")
        return value or "live"

    @property
    def timeframes(self) -> list[str]:
        return [t.strip() for t in self.TIMEFRAMES.split(",") if t.strip()]

    @property
    def exchanges_list(self) -> list[str]:
        return [e.strip().lower() for e in self.EXCHANGES.split(",") if e.strip()]

    @property
    def secondary_exchanges_list(self) -> list[str]:
        return [e.strip().lower() for e in self.SECONDARY_EXCHANGES.split(",") if e.strip()]

    @property
    def watchlist(self) -> list[str]:
        out: list[str] = []
        for s in self.WATCHLIST_SYMBOLS.split(","):
            s = s.strip().upper()
            if s and s not in out:
                out.append(s)
        return out

    @property
    def allowed_user_ids(self) -> list[int]:
        out: list[int] = []
        raw = f"{self.TELEGRAM_ALLOWED_USER_IDS},{self.TELEGRAM_ADMIN_CHAT_ID},{self.TELEGRAM_CHAT_ID}"
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value and value not in out:
                out.append(value)
        return out

    @property
    def db_path(self) -> Path:
        if self.DB_PATH:
            return Path(self.DB_PATH)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        return self.DATA_DIR / "signals_ultimate.db"

    @property
    def horizon(self) -> str:
        tfs = self.timeframes
        return f"{tfs[0]}-{tfs[-1]}" if tfs else "15m-4h"

    @property
    def bot_confidence_weights(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for chunk in str(self.BOT_CONFIDENCE_WEIGHTS or "").split(","):
            key, sep, value = chunk.partition(":")
            key = key.strip().lower()
            if not sep or key not in DEFAULT_CONFIDENCE_WEIGHTS:
                continue
            try:
                parsed = float(value)
            except ValueError:
                continue
            if parsed >= 0:
                out[key] = parsed
        for key, default in DEFAULT_CONFIDENCE_WEIGHTS.items():
            out.setdefault(key, default)
        total = sum(out.values())
        if total <= 0:
            return dict(DEFAULT_CONFIDENCE_WEIGHTS)
        return {k: v / total for k, v in out.items()}

    @property
    def alert_chat_ids(self) -> list[str]:
        raw = self.ALERT_CHAT_IDS or self.TELEGRAM_ADMIN_CHAT_ID or self.TELEGRAM_CHAT_ID
        out: list[str] = []
        for part in str(raw or "").split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
        return out

    @property
    def api_token(self) -> str:
        return self.API_TOKEN or self.V3_API_TOKEN


def validate_config(cfg: Settings | None = None) -> list[str]:
    cfg = cfg or load_config()
    errors: list[str] = []
    mode = str(cfg.MARKET_DATA_MODE).lower()
    if mode not in ("live", "auto"):
        errors.append(f"MARKET_DATA_MODE={cfg.MARKET_DATA_MODE!r} invalid: live|auto")
    if not (0 < cfg.SCAN_LIST_QUALITY_MIN <= cfg.SCAN_SHOW_QUALITY_MIN <= 100):
        errors.append("SCAN_LIST_QUALITY_MIN must be in (0, SCAN_SHOW_QUALITY_MIN]")
    if not (0 < cfg.MIN_RISK_REWARD_REVERSAL <= cfg.MIN_RISK_REWARD):
        errors.append("MIN_RISK_REWARD_REVERSAL must be in (0, MIN_RISK_REWARD]")
    tfs = cfg.timeframes
    if not tfs:
        errors.append("TIMEFRAMES empty")
    else:
        known = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
        unknown = [t for t in tfs if t not in known]
        if unknown:
            errors.append(f"TIMEFRAMES unsupported: {', '.join(unknown)}")
    for name, value in (
        ("SCAN_MIN_TURNOVER_USD", cfg.SCAN_MIN_TURNOVER_USD),
        ("QUALITY_MIN", cfg.QUALITY_MIN),
        ("MIN_RISK_REWARD", cfg.MIN_RISK_REWARD),
    ):
        if value <= 0:
            errors.append(f"{name}={value} must be positive")
    if not (0 < cfg.BOT_CONFIDENCE_LOW_MIN < cfg.BOT_CONFIDENCE_MEDIUM_MIN < cfg.BOT_CONFIDENCE_HIGH_MIN <= 100):
        errors.append("BOT_CONFIDENCE scale must be increasing 0..100")
    try:
        cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = cfg.DATA_DIR / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        errors.append(f"DATA_DIR not writable: {exc}")
    return errors


def load_config(**overrides: object) -> Settings:
    cfg = Settings()
    for k, v in overrides.items():
        if v is not None:
            setattr(cfg, k, v)
    return cfg
