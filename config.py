import os

# ── BYBIT API ──────────────────────────────────────────────────────
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# ── TELEGRAM ───────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── ТОРГОВЛЯ ───────────────────────────────────────────────────────
SYMBOL   = "HYPEUSDT"
LEVERAGE = 10          # ← знижено з 20 до 10
CATEGORY = "linear"

# ✅ 10 РІВНІВ для $1,000
MARGINS = [
    40, 46, 53, 61, 70,
    80, 92, 106, 122, 140
]

ENTRY_DROP_PCT     = 0.6
AVERAGING_STEP_PCT = 1.2   # менший крок = плавніше

SMART_TP = [
    0.7, 0.8, 1.0, 1.0, 1.0,
    1.0, 1.0, 1.0, 1.0, 1.0
]

STOP_LOSS_PCT        = 15.0  # менший бо плече 10x
STOP_LOSS_BACKUP_PCT = 17.0

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
DEMO_BALANCE = 1000.0     # ← $1,000 старт

# ── ФАЙЛИ ──────────────────────────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "/tmp/blackhorn")
os.makedirs(DATA_DIR, exist_ok=True)

STATS_FILE      = os.path.join(DATA_DIR, "stats.json")
STATE_FILE      = os.path.join(DATA_DIR, "state.json")
HISTORY_FILE    = os.path.join(DATA_DIR, "history.json")
DEMO_STATE_FILE = os.path.join(DATA_DIR, "demo_state.json")

# ── НАЛАШТУВАННЯ ───────────────────────────────────────────────────
CHECK_INTERVAL    = 5
HEARTBEAT_MINUTES = 120
HISTORY_PAGE_SIZE = 8
BOT_VERSION       = "3.3.0"

# ── ФАНДИНГ ────────────────────────────────────────────────────────
FUNDING_HOURS        = [0, 8, 16]
DEFAULT_FUNDING_RATE = 0.0001
