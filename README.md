# HYPE Futures Signal Intelligence

Единый production-движок для **USDT/USDC perpetual futures**: сканер вселенной,
multi-timeframe анализ, regime detection, производные/funding/order-flow,
детерминированный `LONG / SHORT / NO-TRADE` gate, explainable AI-слой,
walk-forward валидация, lifecycle + SQLite, Telegram beginner/pro,
observability и безопасный read-only API.

> Бот **не торгует**. Это аналитический сигнальный слой; исполнение ордеров
> полностью отделено и в этом движке не реализовано.

## Что было объединено

- **v1 (`src/`)** — сохранено как *общее ядро данных*: failover-источники
  Bybit / Binance / MEXC, публичные тикеры/свечи/фандинг/стакан/новости,
  демо-рынок, индикаторы и волновая структура. Всё остальное (старый
  CEX-советник, API, дашборд, отдельный Telegram-бот, charts) удалено.
- **v2 (`v2/`)** — старый DEX/микрокап-сканер удалён. Полезная идея
  «трёхуровневый скан → junk-фильтр → глубокий анализ» перенесена в
  `v3/scanner.py` для CEX-фьючерсов.
- **v3 (`v3/`)** — единственный движок: `python -m v3 ...` и `python main.py ...`.

## Возможности

- **Автономный скан** USDT-perp без жёсткого watchlist: отсев мусора по
  turnover/спреду, heat-ранжирование, глубокий анализ топ-N.
- **Multi-timeframe** 1m/5m/15m/1h/4h (настраивается).
- **Regime detection**, derivatives (funding, OI, liquidations), order flow,
  BTC/global context.
- **NO-TRADE — полноценная рекомендация**: движок объясняет, почему входить рано.
- **AI explainability**: rule-based по умолчанию + опциональный OpenAI.
  Слой **не может** изменить direction, entry/stop/targets, score или confidence.
- **Stale-data gate**: ticker/свечи старше `MAX_DATA_AGE_SECONDS` → NO-TRADE.
- **Walk-forward** с fees/slippage/funding и вердиктом стабильности.
- **Калибровка порогов** on-sample (read-only отчёт).
- **Signal lifecycle**: cooldown, max-active, TP1/TP2/TP3/SL исходы в SQLite.
- **Telegram**: `/signal ... pro`, `/scan`, `/scan pro`, `/walkforward`, `/status`,
  + уведомления watcher'а о сигналах и закрытиях.
- **API**: `/health`, `/api/v3/*`, Swagger, опциональный `X-API-Token`.
- **Observability**: latency, analyses, scans, errors в `/health` и `/status`.

## Архитектура

```
src/data/ (Bybit/Binance/MEXC failover + demo, indicators, models)
src/analysis/waves.py (structure/volatility helpers)
src/core/ + src/config/ (logging, time, errors, settings)
        │
        ▼
v3.data.FuturesDataService   (validation, stale detection, normalisation)
        │
        ▼
v3.analysis.*                (timeframes, regime, derivatives, orderflow, context, scoring, levels, risk)
        │
        ▼
v3.engine.FuturesSignalEngine → deterministic NO-TRADE gate
        │
        ├── LONG / SHORT
        └── WAIT / NO_TRADE
        │
        ▼
v3.store + SignalLifecycle   (SQLite, cooldown, active book)
        │
        ▼
v3.report / v3.telegram / v3.api / v3.cli
```

Подробности модулей: [`v3/README.md`](v3/README.md).

## Быстрый старт

```bash
git clone https://github.com/artyr180598-gif/hype-martingale-bot
cd hype-martingale-bot

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # заполни TELEGRAM_BOT_TOKEN при необходимости

# полный движок: API + watcher + Telegram
python -m v3 daemon

# или по частям
python -m v3 serve         # FastAPI
python -m v3 watch         # lifecycle-наблюдатель
python -m v3 bot           # Telegram-бот + watcher
```

## CLI

```bash
python -m v3 signal BTCUSDT                  # анализ/сигнал (beginner)
python -m v3 signal BTCUSDT --mode pro       # полный факторный разбор
python -m v3 scan --mode pro                 # скан вселенной USDT-perp
python -m v3 backtest BTCUSDT --tf 15m --bars 2000
python -m v3 walkforward BTCUSDT --tf 15m --bars 5000 --folds 5
python -m v3 calibrate BTCUSDT,ETHUSDT,SOLUSDT --tf 15m --bars 2000
python -m v3 watch BTCUSDT,ETHUSDT
python -m v3 daemon --port 8400              # API + watcher + Telegram
python -m v3 status                          # сигналы + health

# то же через main.py
python main.py status
```

## REST API (порт 8400)

| Метод | Путь | Что возвращает |
|-------|------|----------------|
| GET | `/health` | режим, сигналы, health snapshot |
| GET | `/api/v3/status` | счётчики, последний цикл, ошибки |
| POST | `/api/v3/scan?limit=&top=` | авто-скан вселенной, `tradable` |
| GET | `/api/v3/signal/{symbol}?refresh=true` | полный сигнал |
| GET | `/api/v3/history/{symbol}` | история сигналов |
| POST | `/api/v3/track` | `{SYMBOL: price}` → TP/SL события |
| GET | `/api/v3/backtest/{symbol}?tf=&bars=&warmup=` | бэктест |
| GET | `/api/v3/walk-forward/{symbol}?tf=&bars=&folds=...` | walk-forward |
| GET | `/api/v3/calibrate?symbols=&tf=&bars=` | read-only калибровка |
| GET | `/api/v3/explain/{uid}` | score breakdown для uid |
| GET | `/api/v3/outcomes?symbol=` | lifecycle исходы |

Если задан `V3_API_TOKEN`, тяжёлые эндпоинты требуют заголовок `X-API-Token`.
Swagger: `/docs`.

## Переменные окружения

Ключевые (полный список — [`v3/.env.example`](v3/.env.example)):

| Переменная | По умолчанию | Значение |
|-----------|--------------|----------|
| `MARKET_DATA_MODE` | `auto` | `auto` / `live` / `demo` |
| `PORT` | `8400` | порт HTTP |
| `V3_COMMAND` | `daemon` | `daemon` / `serve` / `watch` / `bot` / `scan` / `signal` / `backtest` / `walkforward` / `calibrate` / `status` |
| `V3_API_TOKEN` | *(пусто)* | если задан — `X-API-Token` обязателен для тяжёлых endpoint'ов |
| `TELEGRAM_BOT_TOKEN` | *(пусто)* | Telegram-бот + уведомления |
| `SCAN_MIN_TURNOVER_USD` | `20M` | минимальный turnover для входа во вселенную |
| `MAX_DATA_AGE_SECONDS` | `90` | stale ticker/candles → NO-TRADE |
| `BACKTEST_FUNDING_RATE` | `0.0002` | консервативный funding cost за 8h в бэктесте |
| `OPENAI_API_KEY` | *(пусто)* | опциональный AI-аннотатор (fallback rule-based) |

## Тесты

```bash
make check   # ruff + pytest
make test    # pytest
```

**24 теста** покрывают анализаторы, scanner, walk-forward, AI reasoning,
stale-data gate, lifecycle, backtest-метрики, калибровку, Telegram-core,
API token guard, отчёты с явным дисклеймером.

## Безопасность

- v3 не может выставить ордер.
- Ключи только из env / `.env`, не в Git.
- AI-слой не может изменить данные/направление/уровни/скор.
- stale data ужесточается до NO-TRADE.
- см. [`SECURITY.md`](SECURITY.md).

## Дисклеймер

Любой анализ/сигнал — **статистическая оценка, а не гарантия результата**.
Все отчёты несут явный дисклеймер. Криптофьючерсы высокорискованны.
