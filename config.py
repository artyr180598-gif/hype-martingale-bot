import os

# ── BYBIT API ──────────────────────────────────────────────────────
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# ── TELEGRAM ───────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── ТОРГОВЛЯ ───────────────────────────────────────────────────────
SYMBOL   = "HYPEUSDT"
LEVERAGE = 10
CATEGORY = "linear"

# Опції плеча для кнопки в Telegram
LEVERAGE_OPTIONS = [3, 5, 6, 10, 20]

# ✅ 10 РІВНІВ (база; при DYNAMIC_MARGINS перераховуються від балансу)
MARGINS = [
    40, 46, 53, 61, 70,
    80, 92, 106, 122, 140
]

# Динамічні рівні — перераховувати від балансу
DYNAMIC_MARGINS = True
RISK_PERCENT    = 85.0   # % балансу на всі рівні

ENTRY_DROP_PCT     = 0.6
AVERAGING_STEP_PCT = 1.2

SMART_TP = [
    0.7, 0.8, 1.0, 1.0, 1.0,
    1.0, 1.0, 1.0, 1.0, 1.0
]

STOP_LOSS_PCT        = 15.0
STOP_LOSS_BACKUP_PCT = 17.0

# ── ЗАХИСТ ВІД ЛІКВІДАЦІЇ ─────────────────────────────────────────
LIQ_PROTECTION_ENABLED = True
LIQ_BUFFER_PCT         = 2.0   # закрити якщо ціна в 2% від ліквідації

# ── КОМІСІЇ ────────────────────────────────────────────────────────
COMMISSION_PCT   = 0.1
MAX_SLIPPAGE_PCT = 0.5
QTY_PRECISION    = 2
PRICE_PRECISION  = 3

# ── API ────────────────────────────────────────────────────────────
API_MAX_RETRIES = 3
API_RETRY_DELAY = 1.0

# ── ДЕМО ───────────────────────────────────────────────────────────
DEMO_MODE    = True
DEMO_BALANCE = 5000.0

# ── ФАЙЛИ ──────────────────────────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "/tmp/blackhorn")
os.makedirs(DATA_DIR, exist_ok=True)

STATS_FILE      = os.path.join(DATA_DIR, "stats.json")
STATE_FILE      = os.path.join(DATA_DIR, "state.json")
HISTORY_FILE    = os.path.join(DATA_DIR, "history.json")
DEMO_STATE_FILE = os.path.join(DATA_DIR, "demo_state.json")
SETTINGS_FILE   = os.path.join(DATA_DIR, "settings.json")

# ── НАЛАШТУВАННЯ БОТА ──────────────────────────────────────────────
CHECK_INTERVAL     = 5
HEARTBEAT_MINUTES  = 120
HISTORY_PAGE_SIZE  = 8
STOP_PAUSE_SECONDS = 120
BOT_VERSION        = "3.3.0"

# ── ФАНДИНГ ────────────────────────────────────────────────────────
FUNDING_HOURS        = [0, 8, 16]
DEFAULT_FUNDING_RATE = 0.0001

# ── НОВИНИ ─────────────────────────────────────────────────────────
NEWS_CHECK_MINUTES = 30
NEWS_MAX_ITEMS     = 5
