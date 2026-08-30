# HYPE Advisor — аналитический крипто-советник
FROM python:3.11-slim as builder

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# ── Финальный образ ──
FROM python:3.11-slim as runner

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/root/.local/bin:$PATH" \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ ./src/
COPY v2/ ./v2/
COPY main.py pyproject.toml ./
COPY entrypoint.sh ./
RUN chmod +x ./entrypoint.sh \
    && mkdir -p /app/data

# данные (SQLite, графики) — в volume
# Healthcheck ходит на PORT (Railway/compose задают, иначе 8000)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:${PORT:-8000}/health || exit 1

EXPOSE 8000

# RUN_V2=true → python -m v2 ${V2_COMMAND:-serve}; иначе python main.py
CMD ["./entrypoint.sh"]
