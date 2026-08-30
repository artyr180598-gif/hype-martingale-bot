#!/bin/sh
# Переключатель версий при деплое (Docker / Procfile / compose).
#   RUN_V2=true  → новая архитектура: python -m v2 ${V2_COMMAND:-serve}
#   иначе        → классический CEX-советник: python main.py
set -e
if [ "${RUN_V2}" = "true" ]; then
  exec python -m v2 "${V2_COMMAND:-serve}" "$@"
else
  exec python main.py "$@"
fi
