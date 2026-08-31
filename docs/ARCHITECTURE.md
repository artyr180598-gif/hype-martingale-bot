# Архитектура HYPE v3

## Поток данных

```
Bybit (primary) ── Binance (failover) ── MEXC (failover) ── Demo (auto fallback)
        │
        ▼
src/data/collector.py        единый MarketDataSource-контракт, HTTP с ретраями
                             (429 → backoff + Retry-After), свечи/тикеры/funding/
                             стакан/ликвидции; EnrichedSource = CoinGecko + F&G + новости
        │
        ▼
v3/data.py FuturesDataService  TTL-кэши, параллельный build_bundle, OI/funding
                             история, L/S account ratio (Bybit, 300s TTL),
                             mark/index (из тикера, 0 доп. запросов),
                             market_overview, stale-детекция
        │
        ▼
v3/analysis/*                timeframes (индикаторы+структура), regime, derivatives,
                             orderflow, context (BTC+ETH), scoring, levels, risk
        │
        ▼
v3/engine.py FuturesSignalEngine  детерминированный NO-TRADE gate
        │                    (один код для live и backtest — live/backtest parity)
        ├── v3/publisher.py   второй независимый валидатор перед публикацией
        ├── v3/store.py       SQLite: сигналы, исходы TP/SL, состояние
        ├── v3/watcher.py     фоновый цикл + lifecycle
        │
        ▼
Telegram (v3/telegram.py + v3/tg/*)   FastAPI (v3/api.py)   CLI (v3/cli.py)
```

## Слои

| Слой | Модули | Ответственность |
|---|---|---|
| Data | `src/data/*`, `v3/data.py` | источники, нормализация, кэш, freshness |
| Analysis | `v3/analysis/*` | факторы: индикаторы, структура, деривативы, стакан, контекст |
| Strategy | `v3/engine.py`, `v3/analysis/scoring.py`, `v3/analysis/levels.py`, `v3/analysis/risk.py` | сигнал, скоринг, уровни, риск, гейт |
| Validation | `v3/validator.py`, `v3/publisher.py` | инвариант публикации |
| Storage | `v3/store.py`, `v3/tg/settings.py` | SQLite (сигналы/исходы/настройки) |
| Observability | `v3/observability.py`, `src/core/logging.py` | метрики, health, structured logs |
| UI | `v3/tg/*`, `v3/telegram.py`, `v3/report.py` | inline-клавиатуры, рендеры, авторизация |
| API | `v3/api.py` | read-only REST |
| Backtest | `v3/backtest.py`, `v3/simulation.py`, `v3/walkforward.py`, `v3/calibrate.py` | симуляция, WF, калибровка |

## Ключевые инварианты

1. **Никакого исполнения ордеров.** Движок аналитический; торговля отделена.
2. **NO TRADE — полноценный ответ.** Гейт обязан уметь сказать «нет».
3. **AI не меняет данные**: direction/levels/score фиксируются до AI-слоя.
4. **Backtest/live parity**: `run_backtest` и live используют `evaluate_bundle`.
5. **Без look-ahead**: в бэктесте видны только закрытые бары, вход с открытия
   следующего бара, стоп проверяется пессимистично.
6. **Секреты только из env**; бот закрыт списком `TELEGRAM_ALLOWED_USER_IDS`.
7. **Данные свежие**: ticker/свечи старше TTL → degraded → NO TRADE; в отчёте
   всегда timestamp.

## Telegram UI

* `v3/telegram.py` — `V3Core` (чистая логика, тестируемая) + `V3TelegramTransport`
  (aiogram: авторизация, сообщения, callback-query, редактирование).
* `v3/tg/keyboards.py` — inline-клавиатуры и payload-схема колбэков.
* `v3/tg/render.py` — списки сетапов, «Мой рынок», глоссарий, настройки.
* `v3/tg/settings.py` — per-user настройки (режим отчёта, депозит, риск).

Callback-схема: `menu`, `scan`, `list:{top|longs|shorts}:{page}`, `coin:{SYM}`,
`update:{SYM}`, `pro:{SYM}`, `market`, `pick:{page}`, `glossary:{term}`,
`settings`, `set:{mode|deposit|risk}:…`, `back:menu`.

## Масштабирование

* Один процесс `daemon` (API + watcher + Telegram), как на Railway.
* Multi-service: `serve` (API) + `watch` (worker) + общий volume `./data`.
* Кэши локальны для процесса; при нескольких инстансах API убедитесь, что
  тяжёлые endpoint'ы защищены `V3_API_TOKEN`.
