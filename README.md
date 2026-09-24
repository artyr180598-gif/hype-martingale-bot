# Hype Martingale Bot — Confluence Engine

This branch contains the full strategy rebuild for the Hype/Martingale bot.

## What was rebuilt

The engine combines independently implemented ideas from established open-source trading frameworks:

- Freqtrade / NostalgiaForInfinity: multi-timeframe confirmation, liquid-volume universe and explicit signal/exit separation.
- Hummingbot: executor-style separation of entry, controlled DCA/recovery and risk barriers.
- SMC-style systems: confirmed market structure, BOS/CHOCH, fair-value-gap and order-block proxies.
- Live execution sanity: order-book imbalance is used only as an extra live/dry-run gate.

The implementation is original code for this repository; it does not copy proprietary or closed-source strategy logic.

## Signal design

A trade is considered only when the 5m setup agrees with 15m and 1h regime filters. The score (0–100) is a ranking score, **not a probability**.

Inputs include:

- EMA trend structure
- RSI
- ATR / volatility extension
- rolling VWAP
- volume participation
- candle-flow proxy
- confirmed swing structure
- BOS / CHOCH
- FVG proxy
- order-block proxy
- live order-book imbalance gate

The engine deliberately rejects stretched entries and contradictory higher-timeframe regimes.

## Recovery / Martingale layer

This is controlled recovery, not blind loss doubling:

- Initial stake: 50 USDT
- Recovery 1: 50 USDT
- Recovery 2: 75 USDT
- Maximum position stake: 175 USDT
- Maximum 2 additional entries
- Recovery is allowed only when the original directional thesis remains valid.
- Leverage never increases because a position is losing.

## Risk

- Bybit USDT futures
- Isolated margin
- Maximum 3 open trades
- Maximum strategy leverage: 3x
- Dry Run enabled by default
- Cooldown, stop-loss guard and maximum-drawdown protections enabled

## Validation

Every strategy change is checked by GitHub Actions for:

1. Docker image build
2. Python syntax
3. Freqtrade strategy discovery
4. Freqtrade configuration validation

Before live trading, the strategy must also pass historical backtesting, lookahead analysis, recursive analysis and a sufficiently long dry-run period. Freqtrade explicitly recommends these validation steps and warns that backtests cannot replace dry-run testing.

## Honest status

No profitability claim is made here. The repository can prove that the strategy loads and passes configuration validation; profitability must be established separately from measured backtest and dry-run results.

