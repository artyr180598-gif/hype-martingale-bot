# Data Architecture

## Canonical timestamps

All persisted backend timestamps are timezone-aware UTC. Exchange event time and local receive time are stored separately when available.

## Data classes

The initial canonical stream covers ticker, trades, candles, order book, funding, open interest and liquidations. Each adapter converts venue-specific payloads into canonical models before downstream processing.

## Quality states

`HEALTHY`, `DEGRADED`, and `UNAVAILABLE` are explicit data states. Invalid prices/quantities, impossible OHLC relationships, stale streams, timestamp gaps and duplicate events are rejected or flagged. A degraded critical dependency suppresses normal trading signals.

## Historical integrity

Historical datasets are stored after normalization. Dataset rows retain source timestamps and venue metadata. Backtests must consume only records whose information was available by simulation time.

## WebSocket resilience

Bybit public linear streams use the official V5 public linear endpoint. The adapter sends heartbeat messages and reconnects with exponential backoff. The official adapter research is recorded in `docs/RESEARCH.md`.

## Order book

Order book consumers must process the initial snapshot and subsequent deltas according to the venue's documented sequencing rules. A new snapshot resets local state.
