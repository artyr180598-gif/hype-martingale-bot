import os

# ── BYBIT API ──────────────────────────────────────────────────────
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# ── TELEGRAM ───────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── ТОРГОВЛЯ ───────────────────────────────────────────────────────
SYMBOL   = "HYPEUSDT"
LEVERAGE = 20
CATEGORY = "linear"

# ✅ 15 УРОВНЕЙ
MARGINS = [
    150, 165, 182, 200, 220,
    242, 266, 293, 322, 354,
    390, 429, 472, 519, 571
]

# Вход при откате -0.6% от максимума
ENTRY_DROP_PCT = 0.6

# Шаг усреднения 1.6%
AVERAGING_STEP_PCT = 1.6

# SmartTP
SMART_TP = [
    0.7, 0.8, 1.0, 1.0, 1.0,
    1.0, 1.0, 1.0, 1.0, 1.0,
    1.0, 1.0, 1.0, 1.0, 1.0
]

# Стоп-лосс
STOP_LOSS_PCT        = 27.0
STOP_LOSS_BACKUP_PCT = 29.0

# ── КОМИССИИ И ТОЧНОСТЬ ───────────────────────────────────────────
COMMISSION_PCT   = 0.1
MAX_SLIPPAGE_PCT = 0.5
QTY_PRECISION    = 2
PRICE_PRECISION  = 3

# ── ДЕМО РЕЖИМ ────────────────────────────────────────────────────
DEMO_MODE    = True
DEMO_BALANCE = 5500.0

# ── ФАЙЛЫ (запасной вариант если Redis недоступен) ────────────────
DATA_DIR = os.getenv("DATA_DIR", "/tmp/blackhorn")
os.makedirs(DATA_DIR, exist_ok=True)

STATS_FILE      = os.path.join(DATA_DIR, "stats.json")
STATE_FILE      = os.path.join(DATA_DIR, "state.json")
HISTORY_FILE    = os.path.join(DATA_DIR, "history.json")
DEMO_STATE_FILE = os.path.join(DATA_DIR, "demo_state.json")

# ── НАСТРОЙКИ БОТА ────────────────────────────────────────────────
CHECK_INTERVAL    = 5
HEARTBEAT_MINUTES = 120
HISTORY_PAGE_SIZE = 8
BOT_VERSION       = "3.1.0"

# ── ФАНДИНГ ───────────────────────────────────────────────────────
FUNDING_HOURS        = [0, 8, 16]
DEFAULT_FUNDING_RATE = 0.0001
