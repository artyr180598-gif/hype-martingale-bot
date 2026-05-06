import os

BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL   = "HYPEUSDT"
LEVERAGE = 10
CATEGORY = "linear"

MARGINS = [150, 225, 338, 506, 759, 1139, 1709]

ENTRY_DROP_PCT     = 1.5
AVERAGING_STEP_PCT = 3.0
TAKE_PROFIT_PCT    = 1.0
STOP_LOSS_PCT      = 22.0

CHECK_INTERVAL  = 10
QTY_PRECISION   = 2
PRICE_PRECISION = 3

# ═══════════════════════════════
# ДЕМО РЕЖИМ
# True  = виртуальная торговля
# False = реальная торговля
DEMO_MODE    = True
DEMO_BALANCE = 5500.0
# ═══════════════════════════════
