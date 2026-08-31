# HYPE Advisor v3 — Futures Signal Intelligence (USDT Perpetual)

Production-oriented signal engine for **USDT-perp futures** (Bybit/Binance/MEXC via
the existing v1 failover data layer). It is deliberately **not** an execution
engine: v3 produces and explains signals; trading/autotrading belongs to a
separate controlled risk layer.

## Why v3 exists

v1 (`src/`) is a mature CEX advisor (multi-source, indicators, backtest/live
parity, Telegram). v2 (`v2/`) is a DEX/micro-cap scanner with on-chain scam
filters. v3 adds the missing **futures-specific production slice**:

* automatic universe scan of perpetuals (not a hard-coded watchlist);
* explicit multi-timeframe pipeline (1m/5m/15m/1h/4h, configurable);
* market-regime detection that changes interpretation of features;
* derivatives analysis (funding, OI, liquidations) and order-flow/liquidity;
* BTC/market context as a modifier (never a standalone trigger);
* interpretable weighted scoring with stored breakdowns;
* deterministic validation / `NO_TRADE` gate;
* signal lifecycle + SQLite audit trail;
* walk-forward backtester with fees/slippage and no look-ahead;
* beginner/pro report formats.

## Architecture

```
Market Data (v1 Bybit/Binance/MEXC failover + demo)
        │
        ▼
 v3.data.FuturesDataService   (validation, stale detection, normalisation)
        │
        ▼
 v3.analysis.timeframes       (1m/5m/15m/1h/4h indicators + structure)
        │
        ├── v3.analysis.regime        (TRENDING/RANGE/HIGH_VOL/...)
        ├── v3.analysis.derivatives   (funding, OI, liquidations)
        ├── v3.analysis.orderflow     (book depth, imbalance, walls, slippage)
        ├── v3.analysis.context       (BTC, dominance, global, sentiment)
        │
        ▼
 v3.analysis.scoring          (interpretable factor score 0..100)
        │
        ▼
 v3.analysis.levels + risk    (ATR entry/SL/TP, risk score, position size)
        │
        ▼
 v3.engine.FuturesSignalEngine  → deterministic validation gate
        │
        ├── LONG / SHORT (CONFIRMED/ACTIVE)
        └── WAIT / NO_TRADE (with reasons)
        │
        ▼
 v3.store + SignalLifecycle   (SQLite, cooldown, active book)
        │
        ▼
 v3.report (beginner/pro)   + v3.api (FastAPI)   + v3.cli
```

## Files

| File | Responsibility |
|---|---|
| `v3/config.py` | all thresholds / timeframes / risk / tier / cooldown. |
| `v3/models.py` | `DataBundle`, `TimeframeView`, `TradingSignal`, breakdowns. |
| `v3/data.py` | facade over `src.data.collector.MarketDataSource`. |
| `v3/analysis/timeframes.py` | indicator + market-structure per TF. |
| `v3/analysis/regime.py` | deterministic market-regime classifier. |
| `v3/analysis/derivatives.py` | funding/OI/liquidation evidence. |
| `v3/analysis/orderflow.py` | book depth, imbalance, slippage proxy. |
| `v3/analysis/context.py` | BTC/global/sentiment context. |
| `v3/analysis/scoring.py` | factor weights, risk penalties, tiers. |
| `v3/analysis/levels.py` | entry zone / SL / TP1-3 / R:R from ATR+structure. |
| `v3/analysis/risk.py` | risk 1..10, volatility-based leverage, position size. |
| `v3/engine.py` | orchestration + deterministic gate (live/backtest parity). |
| `v3/scanner.py` | universe ranking (turnover/heat/spread/funding) + deep analysis. |
| `v3/walkforward.py` | train/test folds, stability verdict (STABLE/MIXED/UNSTABLE). |
| `v3/calibrate.py` | read-only threshold calibration on a live/backtest sample (never edits config). |
| `v3/ai.py` | rule-based explanation layer + optional OpenAI annotator (never changes direction/levels/score). |
| `v3/observability.py` | thread-safe runtime metrics + `/health` snapshot. |
| `v3/validator.py` | pre-publish validation of a `TradingSignal`. |
| `v3/store.py` | SQLite signals/outcomes + cooldown lifecycle. |
| `v3/watcher.py` | background lifecycle observer (`v3 watch`). |
| `v3/backtest.py` | walk-forward with fees, slippage, no look-ahead. |
| `v3/report.py` | beginner / pro Telegram rendering. |
| `v3/api.py` | FastAPI endpoints. |
| `v3/cli.py` | `signal`, `scan`, `backtest`, `walkforward`, `watch`, `bot`, `status`, `serve`. |

## Quick start

```bash
# dependencies (as usual)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# offline demo first
MARKET_DATA_MODE=demo python -m v3 signal BTCUSDT
MARKET_DATA_MODE=demo python -m v3 status

# live/auto (public endpoints, no keys required)
python -m v3 scan
python -m v3 signal SOLUSDT --mode pro
python -m v3 signal SOLUSDT --mode beginner

# backtest + walk-forward + calibration report
python -m v3 backtest BTCUSDT --tf 15m --bars 2000 --warmup 120
python -m v3 walkforward BTCUSDT --tf 15m --bars 5000 --folds 5
python -m v3 calibrate BTCUSDT,ETHUSDT,SOLUSDT --tf 15m --bars 2000

# passive lifecycle observer / telegram / full daemon
python -m v3 watch BTCUSDT,ETHUSDT
python -m v3 bot            # Telegram + watcher
python -m v3 daemon --port 8400   # API + watcher + Telegram

# read-only HTTP
python -m v3 serve --port 8400
curl localhost:8400/health
curl localhost:8400/api/v3/signal/BTCUSDT
curl localhost:8400/api/v3/backtest/BTCUSDT?tf=15m&bars=1000
curl localhost:8400/api/v3/walk-forward/BTCUSDT?tf=15m&bars=5000&folds=5
curl localhost:8400/api/v3/explain/<uid>
curl localhost:8400/api/v3/outcomes
```

## How a signal is produced

1. Data bundle: price, 24h stats, spread, funding, OI, liquidations, order
   book, BTC/global/news.
2. Timeframe views: structure, ADX, RSI, MACD, ATR, volume z, CVD/OBV,
   squeeze, VWAP, support/resistance.
3. Regime: `TRENDING_UP/DOWN`, `RANGING`, `HIGH/LOW_VOLATILITY`,
   `BREAKOUT/BREAKDOWN`, `ACCUMULATION/DISTRIBUTION`, `UNCERTAIN`.
4. Direction vote (slower TFs weighted higher).
5. Entry/SL/TP and risk.
6. Score = weighted factors − risk penalties.
7. Deterministic gate:
   * invalid/missing price, <2 timeframes, no book liquidity, wide spread,
     low 24h turnover, critical risk, timeframe conflict, low quality;
   * if R:R `< MIN_RISK_REWARD`, risk `> MAX_RISK_SCORE_TO_ENTER`, quality
     `< QUALITY_MIN` → **NO TRADE**.
8. If it survives → `LONG/SHORT`, tier S/A/B/C, lifecycle, report.

## Scoring factors

| Factor | Max points |
|---|---|
| Trend alignment | 15 |
| Market structure | 15 |
| Momentum | 15 |
| Volume | 12 |
| Volatility | 10 |
| Order flow | 10 |
| Derivatives | 10 |
| Liquidity | 6 |
| BTC context | 7 |

Risk penalties: wide spread, overheated funding, timeframe conflicts,
uncertain/high-vol regime, poor R:R, demo data, etc.

## NO TRADE is a feature

The engine is allowed to return nothing. It keeps the analysis, records
`NO_TRADE`, and explains why. It will not fabricate a signal just because a
user requested one.

## Backtesting

* fetches one entry timeframe of history;
* resamples intermediate/macro TFs;
* only **closed** higher-TF bars are visible at each decision;
* same `evaluate_bundle` as live;
* fees 0.055% per side, slippage 0.02%, partial exits, gap-through-stop is a
  loss, and a conservative `BACKTEST_FUNDING_RATE` carry cost is charged per
  held funding interval;
* metrics: trade count, win rate, profit factor, expectancy R, avg win/loss,
  max drawdown, Sharpe, Sortino, `max_consecutive_losses`, `trades_per_day`,
  `precision`, `recall`, `false_positive_rate`, `signals_generated`.

## Scanner (`v3/scanner.py`)

`Scanner` does not filter by a hard-coded watchlist. It:

1. pulls every USDT-perp ticker;
2. drops junk: turnover `< SCAN_MIN_VOLUME_USD`, spread `> MAX_SPREAD_PCT`;
3. ranks by heat (`momentum + log(turnover) + volatility + funding balance +
   spread`), with a small penalty for majors so interesting alts surface;
4. deep-analyzes the top `SCAN_TOP` through the full deterministic engine;
5. persists every analysis snapshot and emits `tradable` only if the gate passes.

## Walk-forward (`v3/walkforward.py`)

`walk_forward` splits history into `WARMUP → TRAIN → TEST` folds and runs the
same `run_backtest` on every fold. It then reports aggregate metrics plus
`STABLE` / `MIXED` / `UNSTABLE` from consistency of per-fold expectancy.
A single lucky fold is not trusted.

## Calibration (`v3/calibrate.py`)

`python -m v3 calibrate BTCUSDT,ETHUSDT,SOLUSDT` runs the same `run_backtest`
across a sample and reports win-rate, expectancy R, max consecutive losses,
average quality/confidence/R:R and tier distribution per symbol. It then prints
**suggestions only** — it never writes `SignalConfig`, and the whole report must
be revalidated with walk-forward before any threshold is changed.

## AI layer (`v3/ai.py`)

* `RuleBasedReasoner` is deterministic and free; it explains the setup from the
  same structured features the engine used.
* `OpenAIReasoner` is optional (only when `OPENAI_API_KEY` is set); on any
  exception it falls back to rule-based.
* Both modes **never** modify `direction`, `uid`, `entry_zone`, `stop_loss`,
  `targets`, `score`, `confidence` or `quality`. They only annotate
  `reasons`/`risks`.

## Stale data (live gate)

`DataBundle.data_age_seconds` is derived from ticker `ts_ms`; each analyzed
timeframe also checks how old the newest closed bar is. If the ticker or any
timeframe is older than `MAX_DATA_AGE_SECONDS`, the bundle is marked degraded
and `validate()` returns `NO_TRADE`, guaranteeing no signal is published from
stale data.

## Observability (`v3/observability.py`)

`/health` and `/api/v3/status` expose a `HealthSnapshot`: mode, data_ok,
analyses, scans, avg latency, last error. `RuntimeMetrics` is thread-safe and
local (no external telemetry required); it is meant to be exported to any
monitoring stack later.

## Telegram

Read-only v3 bot (never executes orders):

```bash
python -m v3 bot            # needs TELEGRAM_BOT_TOKEN / TELEGRAM_TOKEN
python -m v3 watch          # background watch + scan + TP/SL outcome tracking
python -m v3 watch BTCUSDT,ETHUSDT
```

Commands: `/help`, `/status`, `/signal BTCUSDT`, `/signal BTCUSDT pro`,
`/scan`, `/scan pro`, `/walkforward BTCUSDT [15m]`. Rendering lives in
`v3/report.py` and `v3/telegram.py`.

## API

See module docstring / Swagger at `/docs`.

## Configuration

Root `.env` and `v3/.env.example` are both read. Key variables:

`MARKET_DATA_MODE`, `TIMEFRAMES`, `ENTRY_TF`, `ANALYSIS_BARS`,
`SCAN_MIN_TURNOVER_USD`, `SCAN_MIN_VOLUME_USD`, `SCAN_TOP`, `SCAN_LIMIT`,
`WATCHLIST_SYMBOLS`, `MAX_DATA_AGE_SECONDS`, `BACKTEST_FUNDING_RATE`,
`AI_ENABLED`, `OPENAI_API_KEY`,
`OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`, `V3_API_TOKEN`, `QUALITY_MIN`,
`CONFIDENCE_MIN`,
`MIN_RISK_REWARD`, `MAX_RISK_SCORE_TO_ENTER`, `ATR_SL_MULTIPLIER`,
`ATR_TP_MULTIPLIER`, `RISK_PER_TRADE_PCT`, `MAX_POSITION_PCT`,
`MAX_LEVERAGE`, `COOLDOWN_SECONDS`, `S/A/B/C_TIER_MIN`.

## Security & principle

* v3 never submits orders; `EXECUTOR_MODE` defaults to paper in v2 and there is
  **no** live execution path in v3.
* API keys are optional and only used by read-only exchange/public endpoints;
  never commit `.env`.
* The AI explanation layer (if added) must not modify market data or override
  the deterministic gate.

## Limitations (honest)

* Order flow is a **public-depth/CVD proxy**, not exchange private order flow.
* Funding/OI/liquidation data depends on exchange endpoints and current
  availability; absence only degrades confidence.
* Backtest on demo data proves mechanics, not edge.
* No guarantee of profit. Quality > quantity.
