# Security

This project is an **analytical** crypto futures system, not an
order-execution platform. The unified engine has no live order path at all.

## What is safe by design

* Never commit `.env`, `data/`, API keys, Telegram tokens or exchange secrets.
  `.gitignore` already excludes `.env`, `*.log`, `data/`.
* Exchange credentials are read only from environment variables; the live
  public sources (Bybit/Binance/MEXC) do not require them.
* The unified signal engine never submits orders and has no order/execution
  module.
* **Telegram is closed**: only `TELEGRAM_ALLOWED_USER_IDS` (fallback numeric
  `TELEGRAM_ADMIN_CHAT_ID`) may use the bot. With no allow-list configured the
  transport denies every user and logs a clear operator warning.
* The AI layer is explanation-only. It cannot change market data, direction,
  levels or score; the deterministic gate always runs first.
* Every published signal passes `v3.publisher.sanitize_for_publish` (which uses
  `v3.validator.validate_for_publish`) on Telegram, API and watcher paths.
* Heavy/expensive API endpoints can be protected with `V3_API_TOKEN`
  (`X-API-Token` header).

## Configuration guidance

| Secret | Where | Required? |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | env / `.env` (not committed) | optional (UI + alerts) |
| `TELEGRAM_ALLOWED_USER_IDS` | env / `.env` (not committed) | **required to open bot access** |
| `OPENAI_API_KEY` | env / `.env` (not committed) | optional (rule-based fallback) |
| `V3_API_TOKEN` | env / `.env` (not committed) | optional (recommended if the API is public) |
| `BYBIT_API_KEY/SECRET`, `BINANCE...`, `MEXC...` | env / `.env` (not committed) | optional, unused by public endpoints |

## Input validation

* Market data is coerced to numeric, de-duplicated and non-finite rows are
  dropped.
* Stale tickers and stale candles are marked degraded and block a live signal
  (`MAX_DATA_AGE_SECONDS`); every report shows its data timestamp.
* Engine gate → validator gate (R:R, risk, confidence, quality, liquidity,
  **stale flag / `data_age_seconds` > TTL / отсутствие биржевого timestamp** —
  «real-market-data» инвариант (раунд 3): без биржевого timestamp сигнал не публикуется.
* The API is read-only; it does not accept orders or credentials.
* Telegram callback payloads are flat, enumerated strings; user settings are
  bounded (deposit ≤ 1M USD, risk ≤ 5%).
* Secrets are never logged: structured logs carry component/error text, not
  tokens or API keys.

## Reporting a vulnerability

Do not paste secrets or exact account details. Open a private GitHub issue and
describe the affected component.
