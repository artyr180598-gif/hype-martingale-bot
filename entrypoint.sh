#!/bin/sh
# Переключатель версий при деплое (Docker / Procfile / compose).
#   RUN_V3=true  → futures signal intelligence: python -m v3 ${V3_COMMAND:-serve}
#   RUN_V2=true  → новая архитектура: python -m v2 ${V2_COMMAND:-serve}
#   иначе        → классический CEX-советник: python main.py
set -e
if [ "${RUN_V3}" = "true" ]; then
  exec python -m v3 "${V3_COMMAND:-serve}" "$@"
elif [ "${RUN_V2}" = "true" ]; then
  exec python -m v2 "${V2_COMMAND:-serve}" "$@"
else
  exec python main.py "$@"
fi
