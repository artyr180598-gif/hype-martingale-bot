#!/bin/sh
# Единый движок v3 (Futures Signal Intelligence).
#   V3_COMMAND=daemon (default) → FastAPI + watcher + Telegram в одном процессе
#   V3_COMMAND=serve           → только FastAPI
#   V3_COMMAND=watch            → только фоновый lifecycle-наблюдатель
#   V3_COMMAND=bot              → Telegram + watcher
# Остальные команды (signal/scan/backtest/… ) можно передать напрямую.
set -e
exec python -m v3 "${V3_COMMAND:-daemon}" "$@"
