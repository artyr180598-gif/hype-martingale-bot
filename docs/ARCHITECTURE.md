# Architecture

## Status

Phase 0 audit is complete. Phase 1 establishes the production-grade foundation while preserving the existing implementation on `main`.

## Product boundary

The system is a crypto futures market-intelligence and research platform. Telegram is a UI, not the trading core. Live execution is disabled by default and is not part of the initial production path.

## Target flow

```text
Exchange REST/WebSocket
        |
        v
Market Data Adapters -> Data Quality -> Normalization -> PostgreSQL/Redis
        |
        v
Feature Engine -> Regime/Structure/Order Flow/Volatility/Derivatives
        |
        +--> News/Sentiment/Event Intelligence
        |
        v
Strategy Registry -> Ensemble -> Signal Suppression -> Risk Engine
        |
        +--> Historical Analogs / Backtesting / Walk-Forward / Monte Carlo
        |
        v
Paper Trading -> Signal Journal -> Performance/Monitoring
        |
        v
Application API -> Telegram
```

## Service boundaries

- `app/core`: configuration, clocks, errors, logging, identifiers.
- `app/data`: exchange adapters, collectors, normalization and data-quality checks.
- `app/features`: deterministic feature calculations and feature registry.
- `app/market_structure`, `app/order_flow`, `app/volatility`, `app/regimes`: independent analytical engines.
- `app/strategies`: versioned strategy implementations and registry.
- `app/signals`: scoring, ensemble, conflict resolution and NO-TRADE suppression.
- `app/risk`: position sizing, leverage ceiling, invalidation and exposure controls.
- `app/backtesting`: event-driven historical execution with explicit information timestamps.
- `app/validation`: train/validation/test and walk-forward evaluation.
- `app/paper`: realistic paper execution including fees, funding, slippage and latency.
- `app/news`: timestamped news/event ingestion and classification.
- `app/telegram`: presentation and user interaction only.
- `app/monitoring`: health, metrics and structured operational telemetry.

## Data principles

1. Backend timestamps are UTC.
2. A feature at time T may only use information available at or before T.
3. Historical news, funding, OI and order-flow data are timestamp-aligned before backtesting.
4. Missing/stale/degraded data suppresses normal signals.
5. Redis is cache/coordination, never the source of truth.
6. PostgreSQL is the persistent system of record.

## Execution safety

`ENABLE_LIVE_TRADING=false` is the default. Research, signals and paper trading are independent from any future execution service. Exchange API keys must never be committed and must not have withdrawal permission.

## Initial technology decisions

- Python 3.12+.
- FastAPI for internal HTTP API.
- Pydantic v2 for configuration/domain validation.
- SQLAlchemy 2.x async ORM/Core.
- PostgreSQL for persistent data.
- Redis for cache, locks and rate-limit coordination.
- Alembic for migrations.
- pytest, Ruff and mypy for quality gates.
- aiogram for async Telegram integration after the platform core is stable.
- Docker Compose for local reproducible infrastructure.

Dependencies are added only when they provide a concrete capability and are tested in CI.

## Research constraints

The existing Martingale strategy is retained only as a research candidate. It is not assumed to have positive expectancy. Any strategy must pass out-of-sample, walk-forward, cost-aware and Monte Carlo validation before it can be promoted.
