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

# ✅ 17 УРОВНЕЙ — шаг 1.45%
# Сумма маржей = $5151. С комиссиями входа (~2%) ≈ $5254 — укладывается
# в DEMO_BALANCE $5500 с запасом ~$246 на фандинг. Все 17 уровней реально
# открываемы (раньше сумма $6281 > баланса и последние уровни были «мёртвыми»).
MARGINS = [
    127, 139, 153, 169, 186,
    205, 226, 248, 272, 299,
    330, 362, 399, 439, 482,
    531, 584
]

ENTRY_DROP_PCT     = 0.6
AVERAGING_STEP_PCT = 1.45

SMART_TP = [
    0.7, 0.8, 1.0, 1.0, 1.0,
    1.0, 1.0, 1.0, 1.0, 1.0,
    1.0, 1.0, 1.0, 1.0, 1.0,
    1.0, 1.0
]

STOP_LOSS_PCT        = 29.0
STOP_LOSS_BACKUP_PCT = 31.0

# ── ЗАЩИТА ОТ ЛИКВИДАЦИИ ──────────────────────────────────────────
# При плече 20x ликвидация наступает РАНЬШE мягкого стопа -29%.
# Бот аварийно закрывает позицию, когда цена подходит к ликвидации
# ближе чем LIQ_BUFFER_PCT, чтобы спасти остаток депозита.
MAINTENANCE_MARGIN_RATE = 0.005   # ставка поддерживающей маржи (0.5%)
LIQ_BUFFER_PCT          = 2.0     # аварийное закрытие за 2% до ликвидации
LIQ_PROTECTION_ENABLED  = True

# ── КОМИССИИ И ТОЧНОСТЬ ───────────────────────────────────────────
COMMISSION_PCT   = 0.1
MAX_SLIPPAGE_PCT = 0.5
QTY_PRECISION    = 2
PRICE_PRECISION  = 3

# ── API НАСТРОЙКИ ─────────────────────────────────────────────────
API_MAX_RETRIES = 3
API_RETRY_DELAY = 1.0

# ── ДЕМО РЕЖИМ ────────────────────────────────────────────────────
DEMO_MODE    = True
DEMO_BALANCE = 5500.0

# ── ФАЙЛЫ ─────────────────────────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "/tmp/blackhorn")
os.makedirs(DATA_DIR, exist_ok=True)

STATS_FILE      = os.path.join(DATA_DIR, "stats.json")
STATE_FILE      = os.path.join(DATA_DIR, "state.json")
HISTORY_FILE    = os.path.join(DATA_DIR, "history.json")
DEMO_STATE_FILE = os.path.join(DATA_DIR, "demo_state.json")

# ── НАСТРОЙКИ БОТА ────────────────────────────────────────────────
CHECK_INTERVAL     = 5
HEARTBEAT_MINUTES  = 120
HISTORY_PAGE_SIZE  = 8
STOP_PAUSE_SECONDS = 120          # пауза после стопа/аварийного закрытия
BOT_VERSION        = "3.3.0"

# ── ФАНДИНГ ───────────────────────────────────────────────────────
FUNDING_HOURS        = [0, 8, 16]
DEFAULT_FUNDING_RATE = 0.0001
