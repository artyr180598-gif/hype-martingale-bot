# Prime Freqtrade

The previous martingale bot has been removed from this repository.

This repository now runs the official Freqtrade Docker image with a custom PrimeStrategy.

## Safety

The default configuration is Dry Run and futures leverage is capped by the strategy at 3x. Do not switch to live trading until the strategy has been independently backtested and dry-run tested.

## Configuration

Freqtrade supports environment-variable overrides using the FREQTRADE__ prefix. For example:

- FREQTRADE__TELEGRAM__TOKEN
- FREQTRADE__TELEGRAM__CHAT_ID
- FREQTRADE__EXCHANGE__KEY
- FREQTRADE__EXCHANGE__SECRET
- FREQTRADE__EXCHANGE__NAME
- FREQTRADE__DRY_RUN

The dynamic VolumePairList scans the highest-volume markets and refreshes periodically.

Official project: https://github.com/freqtrade/freqtrade

## Current Hype engine

The strategy uses multi-horizon structure, volume/flow proxies, a live order-book microstructure gate when available, and bounded smart recovery. Bybit public-trade orderflow is not enabled because the current Freqtrade 2026.8 runtime reports that trade data is unavailable for Bybit; the bot therefore falls back to candle-flow and live order-book data. The bot remains Dry Run.
