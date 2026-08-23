# Quantitative Crypto Futures Intelligence & Decision-Support Platform

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Code Style](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type Checker](https://img.shields.io/badge/type%20checker-mypy-blue.svg)](https://mypy-lang.org/)
[![Testing](https://img.shields.io/badge/tests-47%20passed-brightgreen.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

A professional, institutional-grade quantitative cryptocurrency futures analytics, signal generation, risk management, and paper trading platform with an interactive Telegram interface and FastAPI REST API.

---

## 📑 Table of Contents
- [1. Paradigm Shift & Mission](#1-paradigm-shift--mission)
- [2. Platform Architecture](#2-platform-architecture)
- [3. Key Subsystems](#3-key-subsystems)
  - [3.1 Data Engine & Quality Assurance](#31-data-engine--quality-assurance)
  - [3.2 Feature Engineering Registry](#32-feature-engineering-registry)
  - [3.3 Market Regime & Multi-Timeframe Engine](#33-market-regime--multi-timeframe-engine)
  - [3.4 Strategy Ensemble & 0–100 Scoring](#34-strategy-ensemble--0100-scoring)
  - [3.5 Conflict Resolution & NO-TRADE Suppression](#35-conflict-resolution--no-trade-suppression)
  - [3.6 Dynamic Risk Engine & Position Sizing](#36-dynamic-risk-engine--position-sizing)
  - [3.7 Event-Driven Backtesting & Walk-Forward Validation](#37-event-driven-backtesting--walk-forward-validation)
  - [3.8 Paper Trading & Signal Journal](#38-paper-trading--signal-journal)
  - [3.9 News, Macro & Sentiment Fusion](#39-news-macro--sentiment-fusion)
  - [3.10 Telegram Bot & Natural Language AI Assistant](#310-telegram-bot--natural-language-ai-assistant)
- [4. Quick Start & Installation](#4-quick-start--installation)
- [5. Configuration & Environment Variables](#5-configuration--environment-variables)
- [6. Docker & Production Deployment](#6-docker--production-deployment)
- [7. Testing & Quality Assurance](#7-testing--quality-assurance)
- [8. API Reference](#8-api-reference)
- [9. Telegram Commands](#9-telegram-commands)

---

## 1. Paradigm Shift & Mission

Legacy cryptocurrency bots frequently rely on dangerous Martingale doubling schemes, grid averaging into liquidation, or naive single-indicator crossovers. 

This platform eliminates dangerous capital-destructive practices and establishes a robust quantitative trading and decision-support architecture:
- **Capital Protection Over Action**: "NO TRADE" is an explicit, preferred outcome when statistical edge is absent or models conflict.
- **Strict Equity-Based Risk**: Trade sizing is determined strictly by account equity, volatility (ATR), and structural invalidation distance — **never** arbitrarily scaled by leverage.
- **Dynamic Structural Leverage**: Leverage recommendations (2x–10x) dynamically scale to guarantee the liquidation price is at least 2.5x beyond the structural stop loss.
- **Zero Lookahead Leakage**: Backtesting and feature pipelines operate with point-in-time state without future bar contamination.
- **Multi-Factor Synergy**: Signals require confluence across market structure (BOS/CHoCH), order flow (CVD/taker delta), derivatives metrics (funding/OI), and macro breadth.

---

## 2. Platform Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        MARKET DATA INGESTION ENGINE                      │
│   Binance Futures  │   Bybit V5   │   CCXT Adapter   │   REST / WebSocket   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    DATA QUALITY & CLEANING PIPELINE                      │
│   Gap Detection    │   Spike Cleansing    │   UTC Resampling & Alignment│
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    ADVANCED FEATURE ENGINEERING REGISTRY                │
│   • Multi-EMAs, ATR, RSI, VWAP      • Liquidity Sweeps & FVGs           │
│   • Cumulative Volume Delta (CVD)   • Funding & OI Z-Scores             │
│   • Market Structure (BOS/CHoCH)    • Volatility Bandwidth Percentiles  │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     MARKET REGIME & MTF ALIGNMENT                       │
│    Macro 4H Trend  ──►  Intermediate 1H Structure  ──►  Trigger 15m Execution│
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     STRATEGY ENSEMBLE ENGINE                            │
│  [TrendFollowing] [Breakout] [MeanReversion] [OrderFlow] [FundingSqueeze]│
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│             QUANTITATIVE 0-100 SCORER & NO-TRADE SUPPRESSION            │
│  Score < 60 ──► NO_TRADE  │ Conflicting Models ──► Scenario Probabilities│
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                 RISK ENGINE & DYNAMIC POSITION SIZING                   │
│   • Structural / ATR Stop Loss      • 3-Tier Take Profit Targets (R/R)  │
│   • Max 1.5% Risk per Trade         • Drawdown Circuit Breakers         │
└──────────────────┬─────────────────────────────────┬────────────────────┘
                   │                                 │
                   ▼                                 ▼
┌──────────────────────────────────┐ ┌───────────────────────────────────┐
│       PAPER TRADING SIMULATOR    │ │        TELEGRAM & REST API        │
│ • Real-time broker simulation    │ │ • Interactive Inline Keyboards    │
│ • MFE / MAE Excursion Tracking   │ │ • Natural Language AI Assistant   │
│ • Slippage, Fees & Margin Lock   │ │ • Signal Cards & Portfolio Viewer │
└──────────────────────────────────┘ └───────────────────────────────────┘
```

---

## 3. Key Subsystems

### 3.1 Data Engine & Quality Assurance
- **Adapters**: High-speed asynchronous connectors for Binance USD-M Futures, Bybit Linear Futures, and extensible CCXT.
- **Data Quality Engine (`DataQualityEngine`)**: Real-time validation checking for missing timestamps, duplicate records, zero/negative volumes, high-low inversion, and abnormal flash wicks. Produces a 0.0–1.0 `DataQualityReport`.

### 3.2 Feature Engineering Registry
- **Technical Indicators**: EMA stacks (9/21/50/200), ATR, RSI (14), Bollinger Bands, SuperTrend, Session VWAP with standard deviation bands.
- **Market Structure (`MarketStructureAnalyzer`)**: Automated Swing High/Low detection, Break of Structure (BOS), and Change of Character (CHoCH).
- **Liquidity & FVGs (`LiquidityEngine`)**: Identifies buy/sell-side liquidity sweeps, Fair Value Gaps (FVGs), and order imbalances.
- **Order Flow (`OrderFlowEngine`)**: Cumulative Volume Delta (CVD), 10-bar CVD slope, buyer/seller taker volume ratios.
- **Derivatives Metrics**: Annualized funding rates, funding z-scores (30-day window), Open Interest z-scores, and basis spread.

### 3.3 Market Regime & Multi-Timeframe Engine
- Classifies market states into 8 distinct regimes:
  - `STRONG_UPTREND`, `WEAK_UPTREND`, `STRONG_DOWNTREND`, `WEAK_DOWNTREND`
  - `RANGE`, `HIGH_VOLATILITY_RANGE`, `BREAKOUT`, `BREAKDOWN`
- **Multi-Timeframe Alignment (`MultiTimeframeEngine`)**: Hierarchically validates that 15m trade direction aligns with 1H trend and 4H macro regime. Counter-trend setups are penalized in scoring.

### 3.4 Strategy Ensemble & 0–100 Scoring
The platform evaluates an ensemble of specialized institutional strategies:
1. `TrendFollowingStrategy` (EMA stack + ADX strength + pullback entry)
2. `BreakoutStrategy` (Squeeze compression + structural BOS expansion)
3. `MeanReversionStrategy` (VWAP statistical extension + RSI exhaustion)
4. `OrderFlowAlphaStrategy` (CVD divergence vs price action)
5. `FundingSqueezeStrategy` (Extreme negative funding + high OI + liquidation exhaustion)
6. `LiquiditySweepStrategy` (Stop hunt wick + instant market displacement)

**0–100 Scoring Breakdown:**
| Factor | Max Points | Description |
| :--- | :---: | :--- |
| **Market Structure** | 15 | BOS / CHoCH confluence, clean swing sequence |
| **Trend & Momentum** | 15 | Multi-EMA stack alignment, ADX > 25, RSI momentum |
| **Order Flow & CVD** | 15 | Delta slope, taker imbalance, CVD divergence |
| **Volatility State** | 10 | Compression squeeze vs expansion runway |
| **Open Interest & Flow**| 10 | OI expansion supporting price direction |
| **Volume Profile** | 10 | Volume > 1.5x 20-period moving average |
| **Momentum Velocity** | 10 | MACD histogram acceleration |
| **Funding & Sentiment** | 5 | Favorable or neutral funding bias |
| **Liquidation Pressure**| 5 | Cascade liquidation exhaustion |
| **Market Breadth** | 5 | Advance/Decline ratio, % assets above EMA 50 |
| **Total Confluence** | **100** | **Grand Score** |

**Tiers:**
- `EXTREME` (Score $\ge 85$): High-conviction institutional confluence.
- `STRONG` (Score $\ge 75$): Standard high-probability setup.
- `VALID` (Score $\ge 65$): Tradable setup with standard sizing.
- `WATCH` (Score $\ge 50$): Forming setup, not ready for entry.
- `NO_TRADE` (Score $< 50$ or conflict): Suppressed setup.

### 3.5 Conflict Resolution & NO-TRADE Suppression
When strategies disagree (e.g. Trend strategy signals LONG while Mean Reversion signals SHORT), the `ConflictResolver` computes probabilistic scenarios (`long_probability_pct`, `short_probability_pct`, `no_trade_probability_pct`). 
The `NoTradeEngine` acts as an uncompromising safety gate:
- Suppresses trades if data quality $< 0.85$.
- Suppresses trades if Risk/Reward ratio $< 1.3$.
- Suppresses trades if high-impact macroeconomic event is imminent ($< 15$ mins).
- Suppresses counter-trend trades during high-volatility regimes.

### 3.6 Dynamic Risk Engine & Position Sizing
- **Risk per Trade**: Configurable (0.75% Conservative, 1.5% Balanced, 2.5% Aggressive of equity).
- **Exact Sizing Formula**:
  $$\text{Quantity} = \frac{\text{Account Equity} \times \text{Risk \%}}{\vert \text{Entry Price} - \text{Stop Loss} \vert}$$
- **Dynamic Leverage Recommendation**:
  $$\text{Leverage} = \min\left(\text{Ceiling}, \frac{60}{\text{Stop Loss \%} \times 2}\right)$$
  Ensures margin liquidation is at least 2.5x further than stop loss.
- **Drawdown Circuit Breaker**: Trading automatically halts if cumulative daily drawdown exceeds 10%.

### 3.7 Event-Driven Backtesting & Walk-Forward Validation
- Realistic simulation with taker fees (0.05%), configurable slippage (0.02%), funding payments, and MMR liquidation mechanics.
- Multi-target partial take-profits (TP1 50%, TP2 30%, TP3 20%) with break-even stop trailing.
- **Walk-Forward Validation (`WalkForwardValidator`)**: Rolling in-sample train / out-of-sample test splits to eliminate curve fitting.
- **Monte Carlo Simulator (`MonteCarloSimulator`)**: 1,000-iteration trade order permutations estimating 95th-percentile Max Drawdown and Sharpe distribution.

### 3.8 Paper Trading & Signal Journal
- Full virtual broker simulation with live order tracking, margin locks, and unrealized PnL updates.
- Post-trade signal journal calculating **MFE** (Maximum Favorable Excursion) and **MAE** (Maximum Adverse Excursion) to continually refine stop/target placement.

### 3.9 News, Macro & Sentiment Fusion
- Asynchronous news ingestion and NLP sentiment classification (`BULLISH`, `BEARISH`, `NEUTRAL`).
- Macroeconomic calendar monitoring (CPI, FOMC, NFP) that triggers volatility warnings and execution dampening prior to high-impact releases.

### 3.10 Telegram Bot & Natural Language AI Assistant
- Interactive Telegram bot built with inline buttons, signal cards, market breadth gauges, and real-time alerts.
- Built-in quantitative AI Assistant (`AIAssistant`) responding to natural language queries regarding market structure, funding bias, risk management, and setup explainability.

---

## 4. Quick Start & Installation

### Prerequisites
- Python 3.11+
- SQLite (default) or PostgreSQL / TimescaleDB
- Redis (optional, for distributed caching)

### Local Setup
```bash
# 1. Clone repository
git clone https://github.com/artyr180598-gif/hype-martingale-bot.git
cd hype-martingale-bot

# 2. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your Telegram bot token and credentials

# 5. Run tests
make test

# 6. Start the unified platform (API + Bot + Scanner)
python -m src.main
```

---

## 5. Configuration & Environment Variables

| Variable | Default | Description |
| :--- | :---: | :--- |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/quant.db` | Async SQLAlchemy database connection string |
| `TELEGRAM_BOT_TOKEN` | `""` | Telegram Bot API token from `@BotFather` |
| `TELEGRAM_ALERT_CHAT_ID` | `""` | Chat or channel ID for automated signal alerts |
| `TELEGRAM_ALERT_MIN_SCORE` | `75.0` | Minimum score threshold to trigger an automated alert |
| `BINANCE_API_KEY` | `""` | Optional Binance API key for market data |
| `BINANCE_API_SECRET` | `""` | Optional Binance API secret |
| `MAX_RISK_PER_TRADE_PERCENT` | `1.5` | Risk percent per setup on balanced profile |
| `MAX_PORTFOLIO_RISK_PERCENT` | `6.0` | Maximum cumulative open risk across all positions |
| `MAX_CONCURRENT_POSITIONS` | `4` | Maximum concurrent open positions |
| `MAX_LEVERAGE_CEILING` | `10` | Hard cap on recommended leverage |
| `ENABLE_LIVE_TRADING` | `false` | Strict safety guard: live trading disabled by default |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `JSON_LOGS` | `true` | Structured JSON log format for production |

---

## 6. Docker & Production Deployment

### Docker Compose
Run the entire stack (TimescaleDB, Redis, FastAPI, Telegram Bot, Background Scanner) with one command:
```bash
docker-compose up -d --build
```

### Systemd Deployment (Linux Server)
```bash
# 1. Copy service configuration
sudo cp deploy/quant_platform.service /etc/systemd/system/quant_platform.service

# 2. Reload and enable service
sudo systemctl daemon-reload
sudo systemctl enable quant_platform
sudo systemctl start quant_platform

# 3. Check logs
journalctl -u quant_platform -f
```

---

## 7. Testing & Quality Assurance

The codebase maintains 100% typing coverage and automated testing:

```bash
# Run pytest test suite
make test

# Run tests with coverage
make test-cov

# Run ruff linter and mypy type checking
make lint

# Autoformat code
make format
```

---

## 8. API Reference

The FastAPI service exposes REST endpoints on port `8000`:

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service health and database connectivity status |
| `GET` | `/api/v1/market/tickers` | 24h ticker prices, volumes, and funding rates |
| `GET` | `/api/v1/market/breadth` | Market breadth state, % above EMA 50, advance/decline |
| `GET` | `/api/v1/signals/latest` | Latest quantitative setups across tracked symbols |
| `POST` | `/api/v1/signals/analyze` | Request full 11-factor quantitative analysis for symbol |
| `POST` | `/api/v1/backtest/run` | Execute on-demand backtest with custom parameters |
| `GET` | `/api/v1/paper/portfolio` | Virtual portfolio balance, open positions, and PnL |
| `POST` | `/api/v1/paper/order` | Execute virtual market or limit order |

---

## 9. Telegram Commands

| Command | Action |
| :--- | :--- |
| `/start` | Open interactive main menu with navigation keyboard |
| `/market` | Comprehensive market overview, funding heatmap, breadth stats |
| `/top` | Top high-conviction quantitative setups currently active |
| `/analyze <SYMBOL>` | Deep 11-factor breakdown, structural levels, scenario probabilities |
| `/backtest` | Historical strategy backtester with interactive parameter selection |
| `/paper` | Virtual trading portfolio, margin utilization, open positions |
| `/news` | Macro news sentiment and economic event calendar |
| `/strategies` | Registry of active quant strategies and status |
| `/settings` | Configure risk profile (Conservative 0.75%, Balanced 1.5%, Aggressive 2.5%) |
| `/help` | Detailed command and feature guide |

---

## ⚖️ Disclaimer
*This software is developed strictly for educational, quantitative research, and decision-support purposes. It does not constitute financial advice. Cryptocurrency futures trading carries substantial financial risk. Always exercise rigorous risk management.*
