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


# ── Сборка (видна пользователю) ─────────────────────────────────
# Раунды 4–5 добавили ранний отбор «намечающегося движения», а версия раньше
# оставалась 3.1.0 — пользователь не видел, что код обновился. Версия и
# подпись раунда теперь печатаются в HELP / меню / настройках / баннере
# старта: по строке сборки видно, какой процесс реально запущен.
APP_VERSION_DEFAULT = "3.2.0"
APP_RELEASE_DEFAULT = "Раунд 5: закрытые свечи и подтверждение раннего импульса"


def build_line(version: str | None = None, release: str | None = None) -> str:
    """Одна строка сборки для всех интерфейсов: «🛠 Сборка: v3.2.0 · Раунд 5: …».

    Функция (а не константа в классе) — чтобы тексты UI собирались без чтения
    env и без риска словить ошибку конфигурации на этапе импорта модуля.
    """
    return f"🛠 Сборка: v{version or APP_VERSION_DEFAULT} · {release or APP_RELEASE_DEFAULT}"


class SignalConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "v3/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ─────────────────────────────────────────────────────
    APP_NAME: str = "FuturesSignalIntelligence"
    APP_VERSION: str = APP_VERSION_DEFAULT          # видно в HELP / меню / настройках / баннере
    APP_RELEASE: str = APP_RELEASE_DEFAULT          # человеческая подпись раунда
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False
    V3_API_TOKEN: str = ""

    # ── Exchange / data mode (reuses v1 settings aliases) ────────
    # Только реальные данные. live — биржи Bybit→Binance→MEXC; auto — те же
    # реальные биржи + реальный спот-контекст (CoinGecko/Fear&Greed/новости).
    # Значение "demo" (и любое другое) — ошибка конфигурации при старте.
    MARKET_DATA_MODE: Literal["auto", "live"] = "live"
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
    SCAN_TOP: int = 20                  # deep-analysis top-N (Stage 2)
    SCAN_LIMIT: int = 250
    SCAN_MIN_TURNOVER_USD: float = 20_000_000.0
    SCAN_MIN_VOLUME_USD: float = 5_000_000.0
    SCAN_SHOW_QUALITY_MIN: float = 72.0  # строгий порог «⭐ ТОП» (A и выше)
    SCAN_LIST_QUALITY_MIN: float = 58.0  # тир-осознанные списки LONG/SHORT (B/C видны)
    SCAN_MAJOR_PENALTY: float = 4.0      # штраф мажоров (по WATCHLIST_SYMBOLS, не хардкод)
    # «намечающееся движение»: ловим до разгона, а не после (+ к heat ранга)
    SCAN_EMERGENCE_ENABLED: bool = True
    SCAN_EMERGENCE_POOL: int = 48        # сколько кандидатов проверяем на emergence (1h свечи)
    SCAN_EMERGENCE_BARS: int = 120
    SCAN_EMERGENCE_BOOST: float = 0.30   # вес готовности импульса в heat
    SCAN_EXCLUDE_EXHAUSTED: bool = True  # не показывать «намечается», если движение уже выжато
    SCAN_AGE_DAYS_MIN: int = 7           # листинг младше → метка fresh (не отсев)
    WATCHER_SCAN_UNIVERSE: bool = True   # daemon сам сканирует вселенную, а не только WATCHLIST
    # диверсификация корзины (долго связанные с конфигом, см. README)
    DIVERSITY_MAX_PER_CLUSTER: int = 1   # сколько кандидатов из одного кластера в Stage 2
    WATCHLIST_SYMBOLS: str = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,LINKUSDT,AVAXUSDT,SUIUSDT,TIAUSDT"

    # ── Timeframes ──────────────────────────────────────────────
    # Mission default mapping: 5m (entry timing) / 15m / 1h / 4h / 1d (macro).
    TIMEFRAMES: str = "5m,15m,1h,4h,1d"          # order matters: fast -> slow
    ENTRY_TF: str = "15m"
    INTERMEDIATE_TF: str = "1h"
    MACRO_TF: str = "4h"
    ANALYSIS_BARS: int = 400
    MIN_BARS: int = 60

    # ── Data freshness / quality ────────────────────────────────
    MAX_DATA_AGE_SECONDS: float = 120.0
    BACKTEST_FUNDING_RATE: float = 0.0002     # per 8h funding interval (0.02%)
    MAX_SPREAD_PCT: float = 0.35
    MIN_SPREAD_PCT: float = 0.005               # absurd quote -> suspicious
    MIN_BID_ASK_LEVELS: int = 5
    MIN_ORDERBOOK_DEPTH_USD: float = 250_000.0
    TICKER_CACHE_TTL_SECONDS: float = 10.0
    KLINES_CACHE_TTL_SECONDS: float = 15.0
    ORDERBOOK_CACHE_TTL_SECONDS: float = 5.0
    FUNDING_CACHE_TTL_SECONDS: float = 300.0
    LIQUIDATIONS_CACHE_TTL_SECONDS: float = 60.0
    LIQUIDATIONS_WS_ENABLED: bool = True     # реальный WS-поток ликвидаций Bybit (public)
    LIQUIDATIONS_WS_MAX_AGE_SECONDS: float = 900.0
    ORDERBOOK_DEPTH: int = 50
    ETH_CONTEXT_ENABLED: bool = True

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
    # emergence (раннее движение): объём/сжатие/консолидация/позиционирование
    EMERGENCE_RVOL_WINDOW: int = 20
    EMERGENCE_RVOL_MIN: float = 1.5
    EMERGENCE_SQUEEZE_LOOKBACK: int = 8
    EMERGENCE_CONSOLIDATION_BARS: int = 12
    EMERGENCE_CONSOLIDATION_ATR: float = 1.6
    EMERGENCE_COMPRESSION_ATR_RATIO: float = 0.80  # ATR ниже 80% своей нормы = сжатие
    EMERGENCE_BREAKOUT_LOOKBACK: int = 20          # предыдущий коридор без текущего бара
    EMERGENCE_MAX_TRIGGER_ATR: float = 0.75         # не преследуем пробой, ушедший дальше
    EMERGENCE_MIN_BREAKOUT_PRESSURE: float = 0.25
    EMERGENCE_MAX_RECENT_MOVE_ATR: float = 2.5      # импульс уже слишком далеко от базы
    EMERGENCE_MIN_ROOM_PCT: float = 0.15            # минимум 15% диапазона до границы
    EMERGENCE_IGNITION_MIN: float = 50.0
    # positioning (OI × funding × цена) — «кто и где стоит»
    OI_CHANGE_BUILD_PCT: float = 2.0
    OI_CHANGE_UNWIND_PCT: float = -2.0
    POSITIONING_QUIET_PRICE_CHANGE_PCT: float = 2.0
    LIQ_ACCELERATION_WINDOW_SEC: int = 300      # «рост ликвидаций за последние N секунд»

    # ── Entry / SL / TP ─────────────────────────────────────────
    ATR_SL_MULTIPLIER: float = 1.8
    ATR_MIN_SL_MULTIPLIER: float = 0.8
    ATR_MAX_SL_MULTIPLIER: float = 3.5
    ATR_TP_MULTIPLIER: float = 3.6
    MIN_RISK_REWARD: float = 1.8
    MIN_RISK_REWARD_REVERSAL: float = 1.5   # разворотные сценарии (CHoCH/sweep) — мягче, но гейт не отключён
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
    # Comma-separated telegram user ids allowed to use the bot. The bot is
    # closed by default: when this list is empty the transport denies every
    # user (fallback: TELEGRAM_ADMIN_CHAT_ID when it is a numeric id).
    TELEGRAM_ALLOWED_USER_IDS: str = ""

    # ── AI explanation layer (optional, used only for annotations) ─
    AI_ENABLED: bool = True
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT_SECONDS: float = 20.0

    @field_validator("MARKET_DATA_MODE", mode="before")
    @classmethod
    def _data_mode_real_only(cls, v: object) -> object:
        """Только реальные данные: demo (и любое иное значение) — ошибка старта."""
        value = str(v).strip().lower() if v is not None else ""
        if value == "demo":
            raise ValueError(
                "Режим MARKET_DATA_MODE=demo удалён: платформа работает только на "
                "реальных данных бирж. Допустимые значения: live | auto."
            )
        if value and value not in ("live", "auto"):
            raise ValueError(
                f"Неизвестный MARKET_DATA_MODE={value!r}: допустимые значения live | auto "
                "(только реальные данные)."
            )
        return value or "live"

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
    def allowed_user_ids(self) -> list[int]:
        """Telegram user ids allowed to interact with the bot.

        Sources (merged, deduplicated):
          * TELEGRAM_ALLOWED_USER_IDS, comma separated;
          * TELEGRAM_ADMIN_CHAT_ID / TELEGRAM_CHAT_ID when it is a numeric id.
        """
        out: list[int] = []
        raw = f"{self.TELEGRAM_ALLOWED_USER_IDS},{self.TELEGRAM_ADMIN_CHAT_ID}"
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
        return self.DATA_DIR / "signals_v3.db"

    @property
    def horizon(self) -> str:
        tfs = self.timeframes
        return f"{tfs[0]}-{tfs[-1]}" if tfs else "15m-4h"


def validate_config(cfg: SignalConfig | None = None) -> list[str]:
    """Startup validation. Returns error messages (empty == OK).

    Checked: timeframes format, entry timeframe presence, positive thresholds,
    allowed-user-ids format, a writable data directory. Never raises — a bad
    value must degrade into a readable startup warning, not a traceback.
    """
    cfg = cfg or load_config()
    errors: list[str] = []
    mode = str(cfg.MARKET_DATA_MODE).lower()
    if mode == "demo":
        errors.append(
            "MARKET_DATA_MODE=demo удалён: платформа работает только на реальных данных (live | auto)"
        )
    elif mode not in ("live", "auto"):
        errors.append(f"MARKET_DATA_MODE={cfg.MARKET_DATA_MODE!r} недопустим: только live | auto")
    if not (0 < cfg.SCAN_LIST_QUALITY_MIN <= cfg.SCAN_SHOW_QUALITY_MIN <= 100):
        errors.append(
            "SCAN_LIST_QUALITY_MIN must be in (0, SCAN_SHOW_QUALITY_MIN] — "
            "списки не могут быть строже «⭐ ТОП»"
        )
    if not (0 < cfg.MIN_RISK_REWARD_REVERSAL <= cfg.MIN_RISK_REWARD):
        errors.append("MIN_RISK_REWARD_REVERSAL must be in (0, MIN_RISK_REWARD]")
    tfs = cfg.timeframes
    if not tfs:
        errors.append("TIMEFRAMES is empty")
    else:
        known = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
        unknown = [t for t in tfs if t not in known]
        if unknown:
            errors.append(f"TIMEFRAMES contains unsupported values: {', '.join(unknown)}")
        if cfg.ENTRY_TF not in tfs and cfg.ENTRY_TF not in known:
            errors.append(f"ENTRY_TF={cfg.ENTRY_TF} is not a supported timeframe")
    for name, value in (
        ("SCAN_MIN_TURNOVER_USD", cfg.SCAN_MIN_TURNOVER_USD),
        ("SCAN_MIN_VOLUME_USD", cfg.SCAN_MIN_VOLUME_USD),
        ("QUALITY_MIN", cfg.QUALITY_MIN),
        ("CONFIDENCE_MIN", cfg.CONFIDENCE_MIN),
        ("MIN_RISK_REWARD", cfg.MIN_RISK_REWARD),
        ("MAX_DATA_AGE_SECONDS", cfg.MAX_DATA_AGE_SECONDS),
    ):
        if value <= 0:
            errors.append(f"{name}={value} must be positive")
    if cfg.MAX_RISK_SCORE_TO_ENTER > 10:
        errors.append("MAX_RISK_SCORE_TO_ENTER must be <= 10")
    if cfg.SCAN_TOP < 1:
        errors.append("SCAN_TOP must be >= 1")
    if cfg.TELEGRAM_ALLOWED_USER_IDS:
        for part in cfg.TELEGRAM_ALLOWED_USER_IDS.split(","):
            part = part.strip()
            if part and not part.lstrip("-").isdigit():
                errors.append(f"TELEGRAM_ALLOWED_USER_IDS contains non-numeric id: {part!r}")
    try:
        cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = cfg.DATA_DIR / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        errors.append(f"DATA_DIR is not writable: {exc}")
    return errors


def load_config(**overrides: object) -> SignalConfig:
    cfg = SignalConfig()
    for key, value in overrides.items():
        if value is not None:
            setattr(cfg, key, value)
    return cfg
