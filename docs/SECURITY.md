# Security

## Secrets

API keys, Telegram tokens and passwords are environment/secret-manager inputs only. They must never be committed to Git. The repository search found no `BYBIT_API_KEY` declaration in tracked source; runtime credentials are expected to be injected by deployment.

## Bybit

The research platform must use read-only/public market-data credentials whenever possible. If authenticated exchange access is later enabled for paper-account metadata, the API key must not have withdrawal permission. Contract trade permissions remain disabled until a separately reviewed execution service exists.

Bybit supports read-only API keys and exposes API-key permission information through V5. Verify the key permissions before enabling any authenticated service.

## Live trading

`ENABLE_LIVE_TRADING=false` is mandatory by default. No signal, backtest or Telegram handler may bypass this feature flag.

## Data safety

Untrusted external payloads are parsed and validated before entering quant calculations. Missing, stale or contradictory critical market data suppresses normal signals.

## Logging

Never log API secrets, signatures, authorization headers, full private payloads or sensitive user settings. Use request IDs and event IDs for correlation.
