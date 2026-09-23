#!/bin/sh
set -e

echo "🚀 HYPE ULTIMATE v4 starting..."
echo "🛠 Сборка: v4.0.0 · ULTIMATE v4: Multi-exchange + STOBB/SBM/JUMP + Liquidity + Confluence"

# Default command is daemon
CMD=${V3_COMMAND:-daemon}
if [ "$1" != "" ]; then
  CMD="$1"
  shift
fi

echo "Command: $CMD $@"

exec python -m src.hype.cli $CMD "$@"
