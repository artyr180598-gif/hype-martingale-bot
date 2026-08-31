# Security

This project is an **analytical** crypto futures system, not an order-execution
platform. The unified engine has no live order path at all.

## What is safe by design

* Never commit `.env`, `data/`, API keys, Telegram tokens or exchange secrets.
  `.gitignore` already excludes `.env`, `*.log`, `data/`.
* Exchange credentials are read only from environment variables; the live
  public sources (Bybit/Binance/MEXC) do not require them.
* The unified signal engine never submits orders and has no order/execution
  module.
* The AI layer is explanation-only. It cannot change market data, direction,
  levels or score; the deterministic gate always runs first.
* Heavy/expensive API endpoints can be protected with `V3_API_TOKEN`.

## Configuration guidance

| Secret | Where | Required? |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | env / `.env` (not committed) | optional |
| `OPENAI_API_KEY` | env / `.env` (not committed) | optional (rule-based fallback) |
| `V3_API_TOKEN` | env / `.env` (not committed) | optional (recommended if the API is public) |
| `BYBIT_API_KEY/SECRET`, `BINANCE...`, `MEXC...` | env / `.env` (not committed) | optional |

## Input validation

* Market data is coerced to numeric, de-duplicated and non-finite rows are
  dropped.
* Stale tickers and stale candles are marked degraded and block a live signal.
* Signals must pass `v3.validator.validate_for_publish` (R:R, risk, confidence,
  quality, liquidity, demo-data checks).
* The API is read-only; it does not accept orders or credentials.
* `V3_API_TOKEN` is compared with the `X-API-Token` header on heavy endpoints;
  `None`/empty keeps local development open.

## Reporting a vulnerability

Do not paste secrets or exact account details. Open a private GitHub issue and
describe the affected component.
