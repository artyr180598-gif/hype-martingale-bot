# Финальный отчёт — HYPE Crypto Market Intelligence & Trading Analysis Platform (v3.1.0)

> Аналитическая система «Market Intelligence»: **read-only** по умолчанию,
> автоторговли нет. Telegram — только интерфейс (UI), не источник данных.
> Дата отчёта: 2026-08-31. Все результаты воспроизводимы из репозитория.

---

## 1. Резюме (Executive Summary)

Проект преобразован из бота-«мартингейл» в профессиональную аналитическую
платформу: двухэтапный пайплайн «быстрый скан вселенной → глубокий анализ
топ-N», детерминированный сигнальный движок с честным ответом
**LONG / SHORT / NO TRADE**, многотаймфреймовый контекст (5m/15m/1H/4H/1D),
рыночная структура, деривативы Bybit (funding, OI, ликвидации, L/S ratio,
mark/index), стакан, риск-менеджмент с плечами, бэктест + walk-forward,
полностью интерактивный Telegram-UI и API. Все 17 пунктов аудита закрыты
(см. `docs/AUDIT.md`); добавлен 18-й инвариант — **проверка stale-данных в
publish-валидаторе**. Итог: 46 тестов зелёные, `ruff` чистый.

**Ключевые факты для оценки:**

| Метрика | Значение |
|---|---|
| Версия | 3.1.0 (`v3/config.py`, `v3/api.py`) |
| Тесты | 46 passed (30 `test_v3.py` + 16 `test_platform.py`) |
| Линтер | `ruff check .` — All checks passed |
| Режимы данных | `demo` (синтетика), `auto` (Bybit→Binance→MEXC→demo), `live` (только биржи) |
| Сигнально-качественные уровни | LONG / SHORT / NO TRADE / WAIT |
| Автоторговля | отсутствует (нет путей исполнения ордеров) |
| Telegram | закрыт по allow-list, deny-by-default |

---

## 2. Цель и результат трансформации

**Исходная цель:** превратить `hype-martingale-bot` в производственную
платформу анализа крипторынка, где Telegram — UI, а не стратегия.

**Результат:**

1. **Аудит ДО кода** — `docs/AUDIT.md`: 17 критических пробелов (P0–P3),
   план модернизации в 7 фаз. Код не менялся до завершения аудита.
2. **Реализация по фазам**: P0 безопасность → P0 UI → P0 надёжность →
   P1 аналитика → P2 контекст → тесты → документация.
3. **Сохранено рабочее ядро** `src/` (Bybit/Binance/MEXC, фейловер, demo),
   удалён мёртвый код (`bundle_to_df`), дубли задокументированы.
4. **Railway-совместимость сохранена**: env-переменные не переименованы,
   новые — с задокументированными значениями по умолчанию
   (`v3/.env.example`), `entrypoint.sh` + `Dockerfile` не сломаны.

---

## 3. Архитектура системы

Полная диаграмма — в `docs/ARCHITECTURE.md`. Кратко:

```
Источники: Bybit (primary) → Binance → MEXC → Demo (фейловер)
   │  EnrichedSource: CoinGecko (movers/global) + Alternative.me (F&G) + CryptoCompare (news)
   ▼
v3/data.py FuturesDataService — валидация, TTL-кэши, параллельный bundle,
   OI/funding-история, L/S-ratio (Bybit), mark/index, market_overview, stale
   ▼
v3/analysis/* — timeframes, structure, regime, derivatives, orderflow, context, levels, risk
   ▼
v3/engine.py — детерминированный NO TRADE gate (one code for live + backtest)
   ├─ v3/publisher.py — второй независимый валидатор (Telegram/API/watcher)
   ├─ v3/store.py — SQLite (сигналы, исходы TP/SL, состояние)
   ├─ v3/watcher.py — фоновый цикл + lifecycle (cooldown, max active)
   ▼
UI: Telegram (v3/tg/*) | API (v3/api.py) | CLI (v3/cli.py)
```

**Инварианты (7):** no-order-execution · NO TRADE is a first-class answer ·
AI не меняет данные · backtest/live parity · no look-ahead · secrets only
from env · fresh-data TTL → degraded → NO TRADE.

---

## 4. Двухэтапный пайплайн

**Stage 1 — быстрый скан вселенной** (`v3/scanner.py`):
- один вызов тикеров биржи (без свечей, без стакана, без новостей на символ);
- детерминированный рейтинг `heat`: импульс (до +30), ликвидность (до +25),
  волатильность (до +15), funding-перегрев (±8), спред (до +6), штраф мажорам (−4);
- отсев: оборот < `SCAN_MIN_TURNOVER_USD`, спред > `MAX_SPREAD_PCT`.

**Stage 2 — глубокий анализ топ-N** (`SCAN_TOP=20`, по умолчанию):
- только кандидаты Stage 1 → `analyze_batch(..., concurrency=4)`;
- каждый символ — полный `DataBundle` (5 ТФ, деривативы, стакан, контекст);
- **адаптивность**: `build_bundle(deep=False)` — light-путь без
  news/movers/account-ratio (используется для быстрых запросов); список
  глубоких сокращается до размера пула Stage 1 (`candidates[:top]`).

Результат: 20 символов → ~5 секунд в demo, без лишних запросов.

---

## 5. Источники данных и API-дисциплина

| Источник | Что даёт | Нагрузка/кэш |
|---|---|---|
| Bybit v5 | свечи, тикеры, funding-история, стакан, OI, mark/index, **L/S account-ratio** | приоритетный; TLS, 429-backoff |
| Binance USDT-M | резервный фейловер | только при отказе Bybit |
| MEXC | второй резерв | только при отказе Bybit×2 |
| CoinGecko | movers, trending, global stats | 600s кэш |
| Alternative.me | Fear & Greed | 900s кэш |
| CryptoCompare | новости | 600s кэш |

Дисциплина (`src/data/collector.py`, `v3/data.py`):
- `_Http.get`: таймаут, **ретрай на 429 с уважением `Retry-After`**,
  экспоненциальный backoff, 404 → `UnknownSymbol`, 5xx → retry;
- семафор параллелизма (8 соединений), TTL-кэши слоя v3
  (tickers 10s, klines 15s, стакан 5s, funding 300s, ликвидации 60s,
  account-ratio 300s, context 120s);
- `build_bundle` — `asyncio.gather` (все части параллельно);
- демо/фейловер — никогда не подменяет «упал live» тихим синтетическим
  сигналом: `is_demo=True` блокирует публикацию.

---

## 6. Многотаймфреймовый анализ

- **По умолчанию `TIMEFRAMES=5m,15m,1h,4h,1d`**, `ENTRY_TF=15m`,
  `ANALYSIS_BARS=400` (настраиваемо).
- Каждый ТФ → `TimeframeView`: trend (up/down/range), ADX, RSI, MACD-hist,
  Stochastic, ATR%, vol_z (волатильность), структура (HH/HL/LH/LL, BOS/CHoCH,
  support/resistance, liquidity zones).
- **Контекстное использование, не изолированные сигналы**: RSI<30 ≠ buy —
  он входит в скоринг только вместе с трендом, структурой, стаканом и
  деривативами (см. §12).
- Конфликт ТФ (up на 5m против down на 4h) → `regime.conflicts` →
  NO TRADE/WATCH (см. §15).
- Горизонт по умолчанию: `ENTRY_TF` → `horizon` (mapping в `v3/config.py`).

---

## 7. Рыночная структура

`v3/analysis/structure.py` и `src/analysis/waves.py` дают:

- **Swing-структура**: последовательные максимумы/минимумы → HH/HL/LH/LL;
- **BOS** (Break of Structure) / **CHoCH** (Change of Character) с
  подтверждением по закрытию;
- **Support/Resistance**: кластеры экстремумов, объёмные уровни;
- **Liquidity zones**: зоны скопления стопов (между свингами), стены стакана
  (`OrderBook.walls`);
- Вход привязывается к структуре: entry zone якорится к support (LONG) /
  resistance (SHORT) / VWAP / EMA50, при этом диапазон удерживается в
  пределах 0.2–1.0 ATR (`v3/analysis/levels.py`).

---

## 8. Объём и волатильность

- **Объём**: 24h turnover/volume в тикере, диапазон ±1% depth в стакане,
  `volume_imbalance` из CVD-прокси (buy/sell pressure);
- **Волатильность**: ATR% на каждом ТФ, `vol_z` (z-score объёма),
  внутридневной диапазон (high−low)/low в Stage 1;
- Фильтры: `SCAN_MIN_TURNOVER_USD` (мин. оборот), минимальный объём,
  `MAX_SPREAD_PCT`, liquidity grade (excellent/ok/thin/empty);
- Низкая волатильность → «нет движения» — часть NO-TRADE-причин (`WAIT`).

---

## 9. Деривативы Bybit

`v3/analysis/derivatives.py` агрегирует:

| Показатель | Источник | Статус |
|---|---|---|
| Funding rate + тренд (rising/falling/overheated_long/short) | `/v5/market/funding/history` | ✅ |
| История funding (12 значений) | тот же эндпоинт | ✅ |
| Open Interest (USD) + изменение 24h | тикер + накопленная OI-история | ✅ |
| Ликвидации (прокси крупных сделок; у Bybit нет публичного REST ликвидаций) | `/v5/market/recent-trade` | ✅ (прокси, задокументировано) |
| **Long/Short account ratio (0..1)** | `/v5/market/account-ratio` (публичный) | ✅ **новое** |
| Mark price / Index price | поля тикера (`markPrice`, `indexPrice`) | ✅ **новое** — 0 доп. запросов |
| Taker buy/sell ratio | через L/S ratio | ✅ (синоним) |

Использование в контексте: funding-перегрев режет score, перекос L/S>0.65
(толпа в лонгах) −8, L/S<0.35 (толпа в шортах) +8; ликвидационный имбаланс
согласуется с funding. Ни один показатель сам по себе не даёт сигнала.

---

## 10. Стакан и ликвидность

`v3/models.py::OrderFlowSnapshot` из `v3/analysis/orderflow.py`:

- `spread_pct` (фильтр: > `MAX_SPREAD_PCT` → NO TRADE);
- `bid_depth_usd` / `ask_depth_usd` в ±1% от mid → `imbalance` (−1..+1);
- `biggest_bid_wall_usd` / `biggest_ask_wall_usd` (стены);
- `liquidity_grade` (excellent/ok/thin/empty — empty → жёсткий блок);
- `slippage_pct` — оценка исполнения объёма против глубины стакана;
- `cvd_trend` — v3-прокси тренда покупок/продаж.

---

## 11. Режимы рынка и BTC-контекст

- **Market regime** (`v3/analysis/regime.py`): TRENDING_UP / TRENDING_DOWN /
  RANGING / VOLATILE / UNKNOWN — из согласованных ADX+структуры ТФ;
- **BTC-контекст** (`v3/analysis/context.py`): 24h %, оборот, funding,
  доминация, глобальное изменение рынка, Fear & Greed;
- **ETH-контекст**: 24h % + funding (новые поля в `DataBundle`);
- Flat/неизвестный BTC-тренд → «caution», не жёсткий блок;
- Режим участвует в скоринге (направление против рынка штрафуется) и в
  бэктест-разбивке (`by_regime`).

---

## 12. Взвешенный сигнальный движок и скоринг

`v3/analysis/scoring.py::score_signal`: веса по факторам (сумма весов
нормализована). Факторы:

- тренд/структура (BOS/CHoCH, HH/HL),
- индикаторы (ADX, RSI, MACD, Stochastic) **в контексте**,
- волатильность (ATR%, vol_z),
- объём/стакан (imbalance, grade, CVD),
- деривативы (funding, OI, L/S, ликвидации),
- BTC/ETH/глобальный контекст + regime,
- качество уровней (R:R, зона входа, инвалидация).

Выход: `score` (0..100) → tier (S/A/B/C/NONE); **`quality`** —
**качество сетапа**, а не вероятность прибыли (это явно написано в UI и
`SECURITY.md`); `confidence` — уверенность в данных (на основе degraded,
диапазона спреда, согласованности ТФ). Детерминированный gate ⇒
AI-слой (`v3/reasoning.py`) только объясняет и никогда не меняет
direction/levels/score.

---

## 13. Вход, стоп, тейки, R:R, инвалидация

`v3/analysis/levels.py::build_levels`:

- **Entry zone**: диапазон, якоренный к структуре (support/resistance/VWAP/
  EMA50), ограниченный 0.2–1.0 ATR от текущей цены;
- **Stop Loss**: `max(ATR×1.8, структура)` с клипом 0.8–3.5 ATR;
- **TP1/TP2/TP3**: ATR-кратные (суммарно ~3.6×ATR целей), с клампингом к
  ближайшим уровням; R:R = расстояние до TP1 / расстояние до SL;
- **Инвалидация**: текстовое описание условия отмены (пробой структуры);
- Фильтр: `MIN_RISK_REWARD=1.8` — ниже → NO TRADE.

---

## 14. Управление рисками

`v3/analysis/risk.py`:

- `RISK_PER_TRADE_PCT` (депозит-процент риска на сделку),
- **левередж-тиры**: капитал → плечо (макс. `MAX_LEVERAGE=10`),
- `risk_score` 0..10 (по волатильности/ликвидациям/спреду/новостям) —
  блок при `MAX_RISK_SCORE_TO_ENTER=6`,
- `position_usd`, `margin_usd`, расчётная цена ликвидации (изолир.),
- `COOLDOWN_SECONDS` (3600) + `MAX_ACTIVE_SIGNALS` (12) через
  `v3/store.py::SignalLifecycle`,
- депозит настраивается в Telegram (⚙️ НАСТРОЙКИ) и в API/CLI (`--deposit`).

---

## 15. NO TRADE и конфликты таймфреймов

- **NO TRADE — полноценный ответ** (всегда с причинами): низкое качество,
  low confidence, R:R ниже минимума, риск выше макс., нет уровней, конфликт
  ТФ, BTC flat (caution), stale-данные, demo-данные, тонкая ликвидность;
- **WAIT** — отдельная категория (есть наблюдения, но нет сетапа);
- Конфликт ТФ (`regime.conflicts`) → violation → NO TRADE;
- Публикация: engine gate → `v3/validator.py` → `v3/publisher.py`
  (sanitize). **Новое**: валидатор дополнительно отклоняет сигналы
  `stale`/`data_age_seconds > MAX_DATA_AGE_SECONDS` (защита в глубину).

---

## 16. Telegram UI

`v3/telegram.py` + `v3/tg/*`:

- **Авторизация**: deny-by-default; `TELEGRAM_ALLOWED_USER_IDS` (+ алиас
  `TELEGRAM_ADMIN_CHAT_ID`); отказ логируется в observable-метрики.
- **Главное меню** (inline кнопки):
  🔎 СКАНИРОВАТЬ РЫНОК · 🧠 АНАЛИЗ РЫНКА · 🔥 ЛУЧШИЕ LONG ·
  🔻 ЛУЧШИЕ SHORT · ⭐ ТОП ВОЗМОЖНОСТИ · 🔍 АНАЛИЗ МОНЕТЫ ·
  📊 МОЙ РЫНОК · ⚙️ НАСТРОЙКИ · 📚 ПОМОЩЬ · 🔄 UPDATE.
- **Callback-роутер**: `menu|scan|scan_result:N|coin:S|analysis:S:N|update:S|
  retry-behind|back:...|glossary:T|settings|set:mode|set:deposit:X|set:risk:X`
  — пагинация («◀️/▶️»), редактирование сообщения (`edit_message_text`),
  кнопка «Назад», «🔄 Обновить» (fresh analysis с `refresh=True`).
- **Settings** (на пользователя, SQLite): beginner/pro режим, депозит
  (100/500/1000/5000/свой), риск % (0.5/1/2) с валидацией границ.
- **❓ЧТО ЭТО?** — глоссарий: RSI, ADX, ATR, BOS/CHoCH, funding, OI,
  liquidity zone, R:R, мартингейл-предупреждение.
- Тексты — `v3/report.py`: beginner vs pro (таблицы факторов, деривативы,
  стакан, риск-бриф), timestamp + stale-предупреждение всегда.

Реализовано как несколько модулей (логика/клавиатуры/рендер/настройки),
что позволяет юнит-тестировать без бота (16 тестов платформы).

---

## 17. Бэктестинг, walk-forward, калибровка

- `v3/backtest.py` — `run_backtest` на **закрытых барах** (no look-ahead),
  вход с открытия следующего бара, стоп проверяется пессимистично;
- **Издержки учитываются**: taker fee + slippage (0.02%) на каждом
  заполнении, funding при удержании через интервалы (`BACKTEST_FUNDING_RATE`);
- `v3/simulation.py` — TP1/2/3, трейлинг-стоп, `r_multiple`;
- **Метрики**: `metrics_from_trades` — win rate, expectancy (R), профит-фактор,
  max drawdown + **разбивка по направлению и по regime** (новое);
- `v3/walkforward.py` — walk-forward: обучающие окна → тестовые окна,
  отчёт по переобучению;
- `v3/calibrate.py` — калибровка порогов по демо/истории;
- CLI: `backtest`, `walkforward`, `calibrate`; API: `/api/v3/backtest`,
  `/api/v3/walk-forward`.

---

## 18. Тестирование и наблюдаемость

**Тесты (46):**

| Группа | Кол-во | Покрытие |
|---|---|---|
| `v3/tests/test_v3.py` | 30 | анализ, скоринг, уровни, риск, движок, бэктест, walk-forward, telegram core, API/handlers |
| `v3/tests/test_platform.py` | 16 | конфиг/env-валидация, 429-retry, TTL-кэш, параллельный bundle, OI-история, **L/S-ratio + mark/index**, light-bundle, структурный entry zone, regime-метрики бэктеста, publisher+stale validation, Telegram auth/callbacks/settings, scanner best-setups |

- `make check` = ruff + pytest; `make test` = pytest.
- **Наблюдаемость** (`v3/observability.py`): метрики запросов, ошибок,
  отказов auth, сканов; `/health` (API) и `pulse` (CLI) показывают режим
  данных, состояние Telegram, последний цикл watcher, ошибки.
- **Logging**: структурированные логи `src/core/logging.py`
  (уровни, тайминги, деградация), предупреждения при stale/ratelimit.

---

## 19. Безопасность и конфиденциальность

`SECURITY.md` + реализация:

1. **Read-only**: нет путей исполнения ордеров; анализ от торговли отделён.
2. **Секреты**: только env/`.env`; `.gitignore` исключает `.env`, `data/`,
   `*.log`; ничего не логируется/не печатается (API/Telegram не показывают
   ключи); ключ Telegram не попадает в `pulse`/`/status`.
3. **Telegram закрыт**: allow-list, deny-by-default, метрика отказов.
4. **API закрыт**: опциональный `V3_API_TOKEN` (Bearer) — для Railway/прокси.
5. **AI-слой не доверен**: не меняет direction/levels/score.
6. **Двойной валидатор публикации**: engine gate → `v3/validator.py`
   (включая stale/возраст данных) → publisher.
7. **Отказ от «гарантий»**: язык UI/доков — «статистический сигнал, не
   гарантия прибыли», Signal Quality ≠ вероятность прибыли.
8. **NO TRADE обязателен**: никогда не форсируется сигнал.

---

## 20. Развёртывание, эксплуатация и ограничения

**Развёртывание (Railway/Docker):**

- Startup: `python -m v3 daemon` (API + watcher + Telegram поллинг в одном
  процессе); `V3_COMMAND=daemon` в `entrypoint.sh`;
- Healthcheck: `GET /health`; порт из `PORT`;
- `DATA_DIR` (SQLite) — volume для multi-replica;
- `.env.example` (root и `v3/`) — полный список переменных с умолчаниями;
  новые переменные добавлены без переименования существующих.

**Известные ограничения (честно):**

- В этой среде из sandbox все биржи дают TLS/SSL EOF — live-проверка
  невозможна; CLI обрабатывает это дружелюбным предупреждением («Нет реальных
  данных» + диагностика + повтор). Демо-режим **удалён в раунде 3**.
- Bybit не отдаёт публичный REST ликвидаций → **раунд 3**: реальные
  ликвидации через публичный WS Bybit v5 (`liquidation.<SYMBOL>`); прокси
  крупных сделок удалён, без потока — «н/д».
- `ts_ms` тикера — клиентское время (receipt), поэтому тикерная стальность
  ограничена; реальная стальность считается по свечам.
- `live`-режим = только биржи (без CoinGecko/новостей/global context):
  `auto` — полный набор.
- Коэффициенты качества/уверенности — аналитические, не вероятности.

---

### Приложения

- **A. Аудит**: `docs/AUDIT.md` (17 пунктов + статусы).
- **B. Архитектура**: `docs/ARCHITECTURE.md`.
- **C. Безопасность**: `SECURITY.md`.
- **D. Руководство**: `README.md`, `v3/README.md`, `v3/.env.example`.
- **E. Скрипты**: `Makefile` (`make check/test/serve/market/bot/...`).

*Отчёт сформирован автоматизированно по состоянию репозитория на 2026-08-31.*

---

## Раунд 3 (2026-08-31): только реальные данные, UX для новичков, история диалога, стратегия

**Мотивация**: устранить любую синтетику из продакшн-путей; сделать вывод понятным новичку;
не затирать историю Telegram; увеличить число честных сетапов без ослабления гейтов.

### 0. Демо-режим удалён полностью

* `src/data/demo.py` удалён; `DemoMarketSource` убран из `build_source`.
* `MARKET_DATA_MODE`: `live` (дефолт) — Bybit→Binance→MEXC; `auto` — те же
  биржи + обогащение (CoinGecko/F&G/CryptoCompare). Значение `demo` (и любое
  иное) — **ошибка конфигурации при старте** с русским текстом («Режим
  MARKET_DATA_MODE=demo удалён…») в `v3/config.py` (pydantic validator +
  `validate_config`) и в фабрике `build_source`.
* Поле `is_demo` удалено из `DataBundle`/`TradingSignal`/сериализации и всех
  потребителей. Валидатор публикации теперь блокирует сигнал **без биржевого
  timestamp** (`data_age_seconds=None`) — инвариант «нет реальных данных →
  нет сигнала»; тот же инвариант в live-пути `engine.validate`.
* **Fail-closed**: без тикера/свечей — `NO_TRADE` + признак `no_data`; UX —
  «⚠️ Нет реальных данных — анализ невозможен» + причины + диагностика по
  каждому источнику (попытки/последняя ошибка/последний успех) + кнопка
  «🔄 ПОПРОБОВАТЬ СНОВА». Ничего не «придумывается» вместо недостающих данных;
  недостающие метрики — «н/д» + понижение confidence.
* **Ликвидации — реальные**: прокси крупных сделок удалён. Новый
  `src/data/liquidations_ws.py`: публичный WS Bybit v5 `liquidation.<SYMBOL>`,
  один коллектор на процесс, реконнект 1→60с, app-ping 20с, буфер 15 минут по
  биржевым `updatedTime`. Поток недоступен → «н/д» (пониженная уверенность,
  без влияния на скоринг).
* Возраст данных — по биржевому timestamp последней свечи входного ТФ.

### 1. Вывод для новичков

Карточка (`render_beginner`): оценка сетапа словами (S «отличный»/A «хороший»/
B «средний»/C «слабый» + легенда в Помощи), «Что делать» (что купить/продать,
зона входа, стоп словами, цели с %, плечо, риск % депозита), «Почему» — 2–3
человеческие фразы из фичей (тренд по ТФ, состояние фандинга, плотность стакана,
конфликт ТФ/сценарий) — БЕЗ внутренних переменных (adx/atr/vol_z/heat — только
в PRO). Дисклеймер «оценка ≠ вероятность прибыли». Метка
`📡 источник · обновлено · возраст` — всегда. Скойт-шапка скана:
«Сканировано N · кандидатов M · сетапов K (A:x B:y C:z) · источник · время» +
агрегированные причины отказов при 0 сетапах.

### 2. История диалога (Telegram)

`BotReply.edit: bool` — правило маршрутизации (см. ARCHITECTURE §Telegram):
независимые запросы — всегда новое сообщение; навигация внутри результата —
правка с fallback на новое при ошибке; `delete_message` не вызывается.
Кнопки и фолбэки не сломаны (dispatch-логика покрыта фейк-транспортом).

### 3. Стратегия: больше качественных сетапов

* `SCAN_LIST_QUALITY_MIN=58` (новый env; 0 < x ≤ `SCAN_SHOW_QUALITY_MIN`):
  тир-осознанные списки; ⭐ ТОП как было — 72.
* Сценарии (`v3/analysis/scenarios.py`): тренд (как раньше) + `reversal_choch`
  (стоп за структурой), `liquidity_sweep`, `range_reversion` (RSI+стакан),
  `breakout_watch` — условный сетап с полем `condition` («вход при закрытии
  выше/ниже X») — ниже по весу, помечен.
* `MIN_RISK_REWARD_REVERSAL=1.5` — только разворотным; качество/риск/спред
  гейты не тронуты; NO TRADE/stale/no-data блокируют как раньше.
* `build_levels` — всегда уровни с честным `auto_fallback` (ATR 1.5% / по ATR от цены).

### Проверки

`make check` зелёный: **72 теста** (46 обновлённых + 26 новых:
`v3/tests/test_realdata.py`, `v3/tests/test_telegram_history.py`).
Live-проверка в sandbox не выполнялась (TLS EOF до всех бирж) — команды для
владельца: `python -m v3 pulse|market|scan --limit 20|signal BTCUSDT|backtest BTCUSDT --tf 15m`.
Калибровка по реальной истории — `python -m v3 backtest <SYM> --tf 15m` /
`calibrate` после деплоя (в sandbox сети нет, синтетикой не заменяли).
