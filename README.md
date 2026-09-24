# Hype Signal Radar

This repository is now a **signal-only crypto market analysis bot**.

It does not open trades, place orders, manage positions, use martingale, or manage a trading balance. Its job is to scan the market, find high-quality setups, and send the user an actionable entry zone with invalidation and targets.

## Research-inspired architecture

The logic was rebuilt after reviewing open-source projects and research around:

- Jesse: multi-timeframe analysis, order-flow features, look-ahead-safe research and signal evaluation.
- SMC projects: confirmed structure, BOS/CHoCH, liquidity sweeps, FVGs, order blocks and premium/discount concepts.
- Order-book/CVD research: imbalance, flow, absorption and microstructure as confirmation rather than standalone signals.
- Bybit market-data tooling: public kline, ticker, order-book, funding/open-interest market data.
- Signal-scoring projects: multiple independent factors are fused into one transparent quality score.

Examples reviewed include:
- https://github.com/jesse-ai/jesse
- https://github.com/cheetah-trade/tradefloor-mcp
- https://github.com/AkhileshSelvan/smc-mcp
- https://github.com/aitradingbotspro/crypto-liquidity-ai-trading-bot
- https://github.com/ymys/Bot-Auto-Screening-Bybit-trading
- https://github.com/nssanta/Elite-Metrics-Trade-Bybit
- https://github.com/JS195/orderflow-alpha
- https://github.com/bybit-exchange/trading-mcp

The implementation in this repository is original. It does not claim that any source strategy is profitable, and it does not copy third-party strategy code.

## Signal pipeline

1. Select liquid USDT perpetuals by 24h turnover.
2. Establish 1h market regime.
3. Confirm direction on 15m.
4. Search for a 5m trigger.
5. Confirm market structure using fully formed swings.
6. Detect BOS, liquidity sweeps, FVG and order-block proxies.
7. Check volume participation and directional candle-flow.
8. Check rolling VWAP location.
9. Use live top-20 order-book imbalance as a secondary confirmation.
10. Reject overextended/chasing entries.
11. Calculate an entry zone, structural stop and three target levels.
12. Require a minimum quality score before Telegram delivery.
13. Suppress repeated identical setups for one hour.

## Telegram signal

Each alert contains:

- LONG or SHORT
- quality score 0-100
- current price
- entry zone
- stop / invalidation
- TP1 / TP2 / TP3
- risk/reward to TP2
- exact reasons supporting the setup
- warnings when a confirmation factor conflicts

The score is a **ranking of confluence**, not a probability and not a promise of profit.

## Honest limitations

The bot is intentionally selective. It can return no signal when the market is mixed or when the setup does not meet the quality gate.

The current implementation uses public Bybit REST market data. Historical CVD and liquidation feeds are not falsely invented from candle data. Where true order-flow data is unavailable, the engine uses clearly labeled proxies instead.

No profitability claim is made without measured historical testing and forward observation.
