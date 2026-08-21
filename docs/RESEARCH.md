# Technology & API Research Baseline

Research date: 2026-08-21.

## Exchange connectivity

The platform will use an exchange-adapter interface. Official exchange APIs are preferred for latency-sensitive WebSocket streams and exchange-specific derivatives fields. CCXT is an abstraction/fallback for supported REST operations where it materially reduces maintenance.

Required adapter capabilities are separated into market-data read access and future execution access. The initial release only enables read access and paper trading.

The adapter contract must cover candles, ticker, trades, order book, funding, open interest, liquidations and exchange metadata where the venue provides the field. Unsupported fields are explicitly marked unavailable; they are never synthesized.

## Binance / Bybit

Official documentation is the source of truth for endpoint names, stream payloads, symbol conventions, rate limits and reconnect behavior. Adapter tests will use captured/synthetic payload fixtures rather than live credentials in CI.

## Application stack

FastAPI + Pydantic v2 provide the API and validation boundary. SQLAlchemy 2.x async is used for database access. Alembic owns schema migrations. PostgreSQL is the persistent store and Redis is cache/coordination.

## Async jobs

Background work will initially use an explicit worker abstraction. A task queue is introduced only where durable scheduling/retries cannot be handled cleanly by the selected worker implementation. Market streams remain long-lived async processes rather than queue jobs.

## Telegram

Telegram is implemented as an adapter around application services. Handlers must not contain quantitative logic or database queries that bypass repositories/services.

## Quantitative stack

NumPy/Pandas or Polars are selected per workload. SciPy/statsmodels/scikit-learn are research dependencies, not runtime requirements for deterministic calculations unless a validated model actually needs them. Indicators should prefer transparent tested implementations over opaque indicator bundles.

## Observability

Structured logs, health/readiness endpoints and Prometheus-compatible metrics are part of the foundation. Grafana is a deployment concern and will consume metrics rather than embed monitoring logic in trading modules.

## Security

Secrets come from environment/secret stores only. No credentials, tokens or private keys belong in source control. Live trading is feature-flagged off.

## Backtesting research rule

Historical information must carry an `available_at`/event timestamp. Backtests consume only data whose availability timestamp is <= simulation time. Same-candle execution ambiguity is resolved conservatively or requires finer-granularity data; it is never silently ordered to make a strategy look better.

## References

- Binance USDⓈ-M Futures API documentation: https://developers.binance.com/docs/derivatives/usds-margined-futures
- Bybit V5 API documentation: https://bybit-exchange.github.io/docs/v5/intro
- CCXT manual: https://docs.ccxt.com/
- FastAPI documentation: https://fastapi.tiangolo.com/
- Pydantic documentation: https://docs.pydantic.dev/
- SQLAlchemy documentation: https://docs.sqlalchemy.org/
- Alembic documentation: https://alembic.sqlalchemy.org/
- PostgreSQL documentation: https://www.postgresql.org/docs/
- Redis documentation: https://redis.io/docs/
- Telegram Bot API: https://core.telegram.org/bots/api
