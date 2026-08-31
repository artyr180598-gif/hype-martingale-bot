# Аудит проекта (до модернизации)

Дата: 2026-08-31. Репозиторий: `artyr180598-gif/hype-martingale-bot` (ветка `arena/01a0589d-hype-martingale-bot`).

## Что уже есть (сохраняем)

* **v3-движок** (`v3/engine.py`) — детерминированный `LONG / SHORT / NO_TRADE`-гейт,
  общий код для live и backtest (backtest/live parity). Хорошая идея — сохраняем.
* **Data kernel** (`src/data/`) — Bybit / Binance / MEXC с фейловером, публичные
  данные без ключей, демо-рынок, нормализация/дедупликация свечей,
  `EnrichedSource` (CoinGecko, Fear&Greed, новости). Сохраняем.
* **Аналитика** (`v3/analysis/*`) — multi-timeframe, regime, derivatives, orderflow,
  scoring с факторным разбором, levels, risk. Сохраняем (дорабатываем).
* **Backtest / walk-forward / calibration** (`v3/backtest.py`, `v3/walkforward.py`,
  `v3/calibrate.py`) — fees/slippage/funding, без look-ahead (вход на открытии
  следующего бара). Сохраняем.
* **Lifecycle + SQLite** (`v3/store.py`) — cooldown, активные сигналы, исходы
  TP1/TP2/TP3/SL. Сохраняем.
* **AI-слой** (`v3/ai.py`) — rule-based по умолчанию + опциональный OpenAI;
  не может менять direction/levels/score. Сохраняем.
* **Observability + API** (`v3/observability.py`, `v3/api.py`). Сохраняем (расширяем).

## Критические проблемы (исправляем)

| # | Проблема | Приоритет |
|---|----------|-----------|
| 1 | **Telegram-бот без авторизации**: любой пользователь, знающий токен бота, может запускать сканы и получать анализ | P0 |
| 2 | **Telegram UI — только текст/команды**: нет main menu, inline-кнопок,
  «Сканировать рынок», «Лучшие LONG/SHORT», «Топ возможности», «Мой рынок»,
  «Настройки», «Помощь», пагинации, «Обновить», «Что это?» | P0 |
| 3 | `_Http.get` **не ретраит HTTP 429** (rate limit) — бросает сразу, хотя
  декларирует exponential backoff | P0 |
| 4 | `v3/validator.py` **не подключён** к публикации (метрика в SECURITY.md утверждает обратное) | P0 |
| 5 | `TIMEFRAMES` по умолчанию `1m,5m,15m,1h,4h` — нет 1D (миссия: 5m/15m/1H/4H/1D) | P0 |
| 6 | `oi_change_24h_pct` всегда `None` — `_oi_history`/`_funding_history` никогда не заполняются | P1 |
| 7 | `build_bundle` делает 6–8 HTTP-запросов **последовательно** (tickers, BTC,
  funding, liquidations, orderbook, news, movers) — латентность и нагрузка | P1 |
| 8 | Ликвидации Bybit: 12 доп. запросов за `build_bundle` (только прокси, без кэша) | P1 |
| 9 | Нет «Мой рынок» / market overview (BTC+ETH+global+Fear&Greed+gainers/losers) | P1 |
| 10 | Entry zone — только 0.5×ATR от текущей цены; нет якоря на структуру
  (support/VWAP/EMA) | P1 |
| 11 | Отчёты не показывают **timestamp данных** и stale-статус; не разбиты на
  SUMMARY→TECHNICAL→DERIVATIVES→TRADE PLAN→RISKS | P1 |
| 12 | Нет адаптивного глубинного анализа (majors vs мелкие альты) на этапе Stage 2 | P1 |
| 13 | Новости: есть sentiment, но нет вывода источника/timestamp/релевантности | P2 |
| 14 | Нет разбивки бэктеста по regime / направлению | P2 |
| 15 | `startup` не валидирует конфигурацию (переменные окружения) | P1 |
| 16 | `bundle_to_df` в `v3/models.py` — мёртвая функция | P3 |
| 17 | `elliott`/`ElliottResult` в `src/analysis/waves.py` не используется движком v3 | P3 |

## Устаревшее/дублирующееся

* `src/config/settings.py` частично дублирует `v3/config.py` — оставляем только
  потому, что `src/data/collector.py` построен на нём (реальный общий слой
  конфигурации; дублирование документируем, не ломаем Railway env).
* `WATCH_INTERVAL_SECONDS` в `Settings` не используется v3 (watcher использует
  `SCAN_INTERVAL_SECONDS`) — документировано как legacy.
* `v3/validator.py` — не удаляем: **подключаем** как инвариант публикации.

## План модернизации

1. **P0 Безопасность**: Telegram allowlist (`TELEGRAM_ALLOWED_USER_IDS` +
  fallback на `TELEGRAM_ADMIN_CHAT_ID`), deny-by-default в реальном транспорте.
2. **P0 UI**: inline-клавиатуры, callback-router, пагинация, «Обновить»,
  глоссарий «Что это?», «Настройки» (депозит/режим/риск), редактирование сообщений.
3. **P0 Надёжность**: retry на 429, кэш TTL, параллельный `build_bundle`,
  конфиг-валидация при старте, публикация через валидатор.
4. **P1 Аналитика**: 1D по умолчанию, ETH-контекст, entry-zone от структуры,
  OI/funding history, market overview, отчёты с timestamp и секциями,
  Stage 2 filtering, regime/direction разбивка бэктеста.
5. **P2**: news-панель (источник/timestamp), glossary, API `/market`, `/top`.
6. **Тесты**: +15 юнит/интеграционных (auth, callbacks, cache, retry, уровни,
  market overview, publisher, config, Bybit L/S endpoint) — итого 46.
7. **Документация**: README, docs/ARCHITECTURE.md, .env.example, SECURITY.md.

## Статус реализации (итог)

| # | Проблема | Статус |
|---|----------|--------|
| 1 | Telegram без авторизации | ✅ `TELEGRAM_ALLOWED_USER_IDS`, deny-by-default, метрика отказов |
| 2 | Telegram UI текст-only | ✅ main menu, callbacks, пагинация, update/pro, glossary, settings |
| 3 | 429 без ретрая | ✅ backoff + Retry-After (`_Http`) |
| 4 | validator не подключён | ✅ `v3/publisher.py` в Telegram/API/watcher; финальным проходом добавлена проверка `stale`/`data_age_seconds` |
| 5 | нет 1D | ✅ `TIMEFRAMES=5m,15m,1h,4h,1d` по умолчанию |
| 6 | OI change всегда None | ✅ история OI/funding накапливается, `oi_change_24h_pct` |
| 7 | build_bundle последовательный | ✅ `asyncio.gather` + TTL-кэши |
| 8 | ликвидации без кэша | ✅ глобальный кэш 60с |
| 9 | нет My Market | ✅ `market_overview()` + `/api/v3/market` + CLI `market` |
| 10 | entry zone без структуры | ✅ якорь на support/resistance/VWAP (0.2–1.0 ATR) |
| 11 | отчёты без timestamp | ✅ `🕐 Данные:`, `⚠️ DATA STALE`, секции плана/рисков |
| 12 | нет адаптивного Stage 2 | ✅ Stage 1 = ranking по тикерам (0 глубоких вызовов на символ), Stage 2 = deep-анализ топ-`SCAN_TOP`; `build_bundle(deep=False)` — light-путь без news/movers/account-ratio |
| 13 | news без источника | ✅ `news_items` (source/ts/sentiment) + панель в PRO отчёте |
| 14 | backtest без regime | ✅ `by_direction`, `by_regime` в метриках |
| 15 | нет валидации конфига | ✅ `validate_config()` на старте CLI, DATA_DIR probe |
| 16 | мёртвый `bundle_to_df` | ✅ удалён |
| 17 | неиспользуемый elliott | остаётся в `src/analysis/waves.py` как библиотечная функция ядра данных (используется zizag/market_structure); удаление затрагивает общий слой — зафиксировано как будущий кандидат, не влияет на v3 |

Новые модули: `v3/publisher.py`, `v3/tg/__init__.py`, `v3/tg/keyboards.py`,
`v3/tg/render.py`, `v3/tg/settings.py`, `v3/tests/test_platform.py`,
`docs/ARCHITECTURE.md`.

Итоговый отчёт по миссии — [`docs/FINAL_REPORT.md`](FINAL_REPORT.md) (20 разделов).

---

## Раунд 3 (2026-08-31): «только реальные данные» + UX

### 0. Удаление демо-режима (высший приоритет)

| Что | Статус |
|---|---|
| `src/data/demo.py` | ✅ удалён из репозитория |
| `DemoMarketSource` в `build_source` | ✅ удалён; `MARKET_DATA_MODE=demo` → `DataSourceError` с русским текстом |
| `MARKET_DATA_MODE` | ✅ дефолт `live`; значения `live` (Bybit→Binance→MEXC) и `auto` (+ CoinGecko/F&G/новости); `demo`/любое другое — ошибка конфигурации при старте (`pydantic field_validator` + `validate_config()` + фабрика) |
| `is_demo` в `DataBundle`/`TradingSignal` | ✅ удалён везде (модели, сериализация, engine/scoring/risk/validator/render) |
| Нет реальных данных → нет анализа | ✅ fail-closed: `NO_TRADE` + features.no_data; TG/CLI/API показывают «⚠️ Нет реальных данных — анализ невозможен» + причины + диагностика по источникам + кнопка «🔄 ПОПРОБОВАТЬ СНОВА» (`update:{sym}` / повторный `scan`) |
| Инвариант публикации | ✅ `engine.validate` (live-путь) и `validator.py` блокируют сигнал без биржевого timestamp (`data_age_seconds=None`) |
| Возраст данных | ✅ по биржевому timestamp последней свечи входного ТФ (тикерный возраст = возраст кэша — fallback) |
| Диагностика источников | ✅ `FailoverSource.diagnostics()` (attempts/consecutive_errors/last_error/last_success_ms), `FuturesDataService.source_diagnostics()`, показ в `pulse`/`/status` |
| Ликвидации | ✅ удалён прокси «крупных сделок»; новый `src/data/liquidations_ws.py` — публичный WS Bybit v5 (`liquidation.<SYMBOL>`): один коллектор на процесс, подписки ≤60, app-ping 20с, reconnect 1→60с; буфер 15 мин с бирж. ts; недоступен → «н/д». Binance REST (`allForceOrders`) остаётся как есть (реальные данные) |

### 1. Вывод для новичков

`v3/report.py render_beginner()`: «Оценка сетапа: X/100 (тир — словами)» +
легенда S/A/B/C в Помощи; блок «Что делать» (купить/продать, зона входа,
стоп словами, цели с %, плечо, риск % депозита); «Почему» — 2–3 человеческие
фразы из фичей (`plain_reasons`), без `adx=/atr=/vol_z` (они остались только
в PRO); обязательные дисклеймеры «оценка ≠ вероятность прибыли»; метка
`📡 {источник} · обновлено HH:MM:SS UTC · возраст Nс` в каждой карточке;
`scan_summary` («Сканировано N · кандидатов M · сетапов K (A:x B:y C:z) ·
источник: … · время») в скане и в хедерах списков; при 0 сетапах —
аггрегированные причины гейт-отказов (`empty_list_hint`).

### 2. История диалога

`BotReply.edit` (default `False` = новое сообщение). Роутинг:
`menu|help|scan|market|coin:*|pick:*|settings` → новое сообщение;
`list:*|update:*|pro:*|glossary:*|set:*|back:menu` → правка; `dispatch_callback`
исполняет правило, `edit_text` имеет fallback на новое сообщение при ошибке
(кроме "message is not modified"); `delete` не вызывается нигде.

### 3. Больше качественных сетапов без потери честности

* `SCAN_LIST_QUALITY_MIN=58` (новый env): тир-осознанные списки показывают
  B/C («средний/слабый» подписано); строгий «⭐ ТОП» остаётся 72 (`SCAN_SHOW_QUALITY_MIN`).
* Новые сценарии (`v3/analysis/scenarios.py`): `reversal_choch` (15m/1h CHoCH,
  стоп за структурой через `stop_override`), `liquidity_sweep`, `range_reversion`,
  `breakout_watch` (условный сетап, `TradingSignal.condition` + рендер
  «вход при закрытии выше X»). Всё — на реальных признаках свечей/стакана.
* `MIN_RISK_REWARD_REVERSAL=1.5` — мягкий R:R только для разворотных; остальные
  гейты (качество/риск/спред) НЕ ослаблены.
* `build_levels` всегда возвращает уровни: fallback «ATR недоступен → 1.5% от
  цены» / «структура недоступна → уровни по ATR от цены» с пометкой `auto_fallback`.
* Адаптивные пороги по режиму: HIGH_VOLATILITY → уменьшенный размер (leverage
  ограничивается ATR как раньше) и предупреждение; диапазон — сценарий
  mean-reversion с доп. фильтром RSI+стакан.

### Тесты (72, `make check` зелёный)

* обновлены: `test_v3.py`, `test_platform.py` (без `is_demo`, валидатор —
  новый инвариант вместо demo).
* новые: `v3/tests/test_realdata.py` (16 тестов: demo-удаление на конфиге/
  фабрике, fail-closed engine/validator, WS-ликвидации через фейк-сессию —
  парсинг/буфер/TTL/подписка/диагностика, честная пустота Bybit REST,
  failover диагностика); `v3/tests/test_telegram_history.py` (10 тестов:
  флаги edit по маршрутам + фейк-транспорт: два скана → два answer, 0 edit,
  delete никогда; fallback update→answer).

### Честно не проверено (sandbox без сети)

* Live-проверка failover/WS/рендеров на реальных данных НЕ выполнялась — в
  sandbox TLS EOF ко всем биржам. Проверить владельцу:
  `python -m v3 pulse`, `python -m v3 market`, `python -m v3 scan --limit 20`,
  `python -m v3 signal BTCUSDT`, `python -m v3 backtest BTCUSDT --tf 15m`.
* Формат кадров WS ликвидаций реализован по спецификации Bybit v5
  (`topic liquidation.<SYMBOL>`, поля `updatedTime/symbol/side/size/price`)
  и покрыт фейк-сессией; подтвердить на живом потоке через `python -m v3 pulse`
  и раздел «Источники» (`bybit-liquidations-ws`).
