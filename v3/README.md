# HYPE Futures Signal Intelligence (USDT Perpetual) — единый движок

The project has **one engine** now. The old v1 CEX-advisor and v2 DEX scanner were
consolidated permanently: v1's **market-data kernel** (`src/data`, `src/core`,
`src/analysis/waves`) is reused as the exchange feed; v2 was deleted and its
useful scanner idea lives in `v3/scanner.py`; all analytics/signals/TG/API live
in `v3`. It is deliberately **not** an execution engine: signal generation and
trading are fully separated.

## Что входит в единый движок

* automatic universe scan of USDT perpetuals (not a hard-coded watchlist);
* explicit two-stage pipeline: fast ticker scan → deep top-N (5m/15m/1h/4h/1d
  by default, configurable `TIMEFRAMES`);
* market-regime detection that changes interpretation of features;
* derivatives analysis (funding history, OI + 24h change, liquidations,
  Bybit long/short account-ratio, mark/index) and order-flow/liquidity;
* BTC/market context as a modifier (never a standalone trigger);
* interpretable weighted scoring with stored breakdowns;
* deterministic validation / `NO_TRADE` gate;
* signal lifecycle + SQLite audit trail;
* walk-forward + read-only threshold calibration;
* clear AI explanation layer (never changes direction/levels/score);
* beginner/pro Telegram and API.

## Architecture

```
Shared data kernel: src/data (Bybit/Binance/MEXC failover — только реальные
данные, demo-источник удалён, Bybit WS ликвидации, indicators),
src/core (logging/time/errors), src/config, src/analysis/waves
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
| `v3/replay.py` | offline run of the LIVE path on a captured exchange snapshot (`v3 replay` / `v3 replay --backtest` / `v3 record`). |
| `v3/report.py` | beginner / pro Telegram rendering. |
| `v3/api.py` | FastAPI endpoints. |
| `v3/cli.py` | `signal`, `scan`, `backtest`, `walkforward`, `watch`, `bot`, `status`, `serve`, `replay`, `record`. |

## Quick start

```bash
# dependencies (as usual)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# самодиагностика источников (только реальные данные; demo-режим удалён)
python -m v3 pulse

# live/auto (public endpoints, no keys required)
python -m v3 market
python -m v3 scan
python -m v3 signal SOLUSDT --mode pro
python -m v3 signal SOLUSDT --mode beginner

# backtest + walk-forward + calibration report
python -m v3 backtest BTCUSDT --tf 15m --bars 2000 --warmup 120
python -m v3 walkforward BTCUSDT --tf 15m --bars 5000 --folds 5
python -m v3 calibrate BTCUSDT,ETHUSDT,SOLUSDT --tf 15m --bars 2000

# прогон движка на РЕАЛЬНЫХ данных без сети (снятый снапшот биржи)
python -m v3 replay v3/tests/fixtures/okx_btcusdt_swap_capture.json
python -m v3 record BTCUSDT --out data/replay/btcusdt.json   # снять свой снапшот

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

### Ранний импульс (до разгона)

Сканер не считает большой `24h %` самостоятельным доказательством. Он
отделяет фазу движения от его размера:

* **EARLY** — сжатая/узкая база, просыпающийся объём и направленное давление,
  при этом до границы диапазона ещё есть место;
* **TRIGGERED** — закрытый бар уже вышел из предыдущего коридора в пределах
  `EMERGENCE_MAX_TRIGGER_ATR`; это начало подтверждённого хода, а не погоня;
* **EXHAUSTED** — цена слишком далеко у экстремума или уже прошла слишком
  много ATR. Такой кандидат остаётся видимым в диагностической heatmap, но
  исключается из обычного раннего Stage 2 и списка «намечается».

RVOL сравнивается со средним **без текущего бара**, а последняя формирующаяся
свеча удаляется на границе data-service. Это защищает от repaint: незакрытый
час не может временно создать ложный всплеск объёма или пробой. Давление
считается по телу свечи, положению close внутри диапазона и последним закрытиям;
простой зелёный бар без подтверждения не считается импульсом.

Фоновый `daemon` по умолчанию запускает этот двухэтапный поиск по всей
ликвидной вселенной (`WATCHER_SCAN_UNIVERSE=true`). Явный `watch
BTCUSDT,ETHUSDT` остаётся точечным режимом. Фаза импульса — лишь независимый
фактор качества и ранжирования, а не замена deterministic NO-TRADE gate.

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
| Trend alignment | 13 |
| Market structure | 13 |
| Momentum | 12 |
| Volume | 12 |
| Volatility | 10 |
| Order flow | 10 |
| Derivatives | 10 |
| Liquidity | 6 |
| Market context (BTC/ETH) | 6 |
| Impulse readiness (emergence) | 8 |

Сумма весов = 100. Таблица сверяется с ``v3/analysis/scoring.py`` — при
изменении весов правьте оба места.

Risk penalties: wide spread, overheated funding, timeframe conflicts,
uncertain/high-vol regime, poor R:R, exhausted impulse, absence of an
exchange timestamp, etc.

### «Уверенность бота» — третья метрика

`v3/analysis/confidence.py` собирает отдельный, явно названный процент:
насколько независимые анализы согласны между собой. Он считается из уже
готового сигнала (без новых запросов), поэтому live, бэктест, API и SQLite
видят одну цифру; разбор пишется в `features.bot_confidence`.

| Компонент | Вес по умолчанию | Источник |
|---|---|---|
| quality | 34% | оценка сетапа |
| data | 16% | полнота/свежесть реальных данных (stale режет вдвое) |
| trend | 16% | доля таймфреймов в сторону сделки + конфликты |
| confirm | 14% | Volume / Order Flow / Derivatives из score breakdown |
| risk | 10% | risk score + потенциал к риску |
| impulse | 10% | ранний отбор (фаза и совпадение направления) |

Это НЕ вероятность прибыли — оговорка печатается рядом с каждой цифрой.
Веса: `BOT_CONFIDENCE_WEIGHTS` (нормируются автоматически).

### Авто-сигналы

`v3/alerts.py` — порог «будить пользователя или нет»: качество
`ALERT_MIN_QUALITY`, уверенность бота `ALERT_MIN_BOT_CONFIDENCE`, полнота
данных `ALERT_MIN_DATA_CONFIDENCE`, риск `ALERT_MAX_RISK_SCORE`, потенциал к
риску `ALERT_MIN_RR`, свежесть, фаза импульса (не `EXHAUSTED`), наличие стопа
и ≥2 целей. `V3Watcher` сохраняет ВСЕ наблюдения, но в чат отправляет только
прошедшие порог (не больше `ALERT_MAX_PER_CYCLE` за цикл, cooldown
`COOLDOWN_SECONDS` на символ). Авто-push — короткая карточка: LONG/SHORT,
уверенность в процентах, 2–3 причины выбора, зона входа, TP/частичная фиксация,
безубыток после TP1 и стоп-инвалидация. Слабые наблюдения не регистрируются как
активные и не могут породить ложное событие TP/SL или заблокировать следующий
сильный сигнал. Пауза/включение и «Проверить сейчас» — в разделе
«🔔 АВТО-СИГНАЛЫ» (callback `alerts`, `alerts:toggle`, `alerts:now`), состояние —
в `GET /api/v3/alerts`.

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

`DataBundle.data_age_seconds` берётся из `ts_ms` тикера; если тикер не дал
биржевой timestamp, возраст считается по входному таймфрейму — от **закрытия**
последней свечи (`open + длительность`), а не от её открытия.

Каждый таймфрейм отдельно проверяется на «отстающий график»: сервис данных
отдаёт только закрытые свечи, поэтому свежие данные — это «последняя закрытая
свеча закрылась не раньше одного таймфрейма назад»
(`now - (last_open + tf) <= tf + MAX_DATA_AGE_SECONDS`). Запас
`MAX_DATA_AGE_SECONDS` нужен, потому что биржа отдаёт только что закрытую свечу
не мгновенно.

История (важно): раньше правило сравнивало **время открытия** свечи с
`tf + MAX_DATA_AGE_SECONDS`. Закрытая часовая свеча по построению старше часа,
поэтому движок объявлял данные устаревшими почти всё время и отвечал `NO_TRADE`
(«stale kline data»). Ошибка не ловилась ни юнит-тестами, ни бэктестом — они
вызывают `evaluate_bundle()` напрямую и не проходят этот участок `analyze()`.
Нашёл её прогон на реальных свечах биржи (`v3/replay.py`, см. «Replay на
реальных данных»); регрессия закрыта в `v3/tests/test_replay_realdata.py`.

Если тикер или график всё же устарели, bundle помечается degraded и `validate()`
возвращает `NO_TRADE` — сигнал из устаревших данных не публикуется.

## Replay на реальных данных (`v3/replay.py`)

Юнит-тесты проверяют логику на синтетических сигналах, живой запуск требует
сеть. Реплей закрывает середину: настоящие свечи, тикер, ставка финансирования,
открытый интерес и стакан с биржи прогоняются через те же кодовые пути, что и
прод (`SnapshotSource → FuturesDataService → FuturesSignalEngine.analyze →
render_signal + assess_confidence + evaluate_alert`), без HTTP-запросов.

```bash
# прогон готового снапшота (реальные свечи OKX BTC-USDT-SWAP, 2026-09-02)
python -m v3 replay v3/tests/fixtures/okx_btcusdt_swap_capture.json
python -m v3 replay <файл> --mode pro --walk 12 --step 2   # + проход по истории
python -m v3 replay <файл> --json

# снять свой снапшот с биржи (нужен доступ к сети), потом гонять его офлайн
python -m v3 record BTCUSDT --out data/replay/btcusdt.json
```

Часы реплея фиксируются на моменте съёма снапшота — иначе данные, свежие на
момент съёма, выглядели бы устаревшими. Цены, объёмы и таймстемпы не меняются.
Чего в снапшоте нет (новости, ликвидации, глобальный контекст, long/short),
того нет и в отчёте: источник отдаёт честную ошибку, и бот показывает «н/д»
вместо выдуманных цифр — таблица «ЧТО В СНАПШОТЕ РЕАЛЬНОЕ» печатается в начале
прогона.

### Бэктест на реальных свечах (`--backtest`)

Реплей отвечает на вопрос «что бы бот сказал в один момент». `--backtest`
отвечает на вопрос «нашёл бы он вообще сетап и чем бы это кончилось»: длинная
серия реальных свечей прогоняется через прод-бэктестер `v3/backtest.py`
(та же комиссия 0.055%, слиппедж 0.02%, пессимистичная проверка стопа,
частичные выходы) — но вместо запросов к бирже история берётся из дословно
снятого файла формата `okx_candles_v1`.

```bash
python -m v3 replay v3/tests/fixtures/okx_btcusdt_15m_300.json --backtest
python -m v3 replay <серия.json> --backtest --warmup 150 --json
```

В репозитории лежит фикстура `okx_btcusdt_15m_300.json`: 300 свечей 15m
BTC-USDT-SWAP с OKX (2026-08-30 09:45 → 2026-09-02 12:30 UTC), последняя свеча
недоформирована (`confirm="0"`) и отбрасывается — заглядывания в будущее нет.

Результат прогона (299 закрытых баров, 100 точек решения) — **отрицательный,
и это важнее красивых цифр**: движок нашёл 5 исполняемых сетапов (все SHORT,
в нисходящем тренде), из них 1 прибыльный; win rate 20%, profit factor 0.345,
матожидание −0.655R, суммарно −3.276R, максимальная просадка 4.36R. Лучший
сетап — качество 61 и уверенность 57.8%, то есть до порога авто-сигнала
(качество ≥78, уверенность ≥70%) ни один не дотянул: живой бот промолчал бы,
что совпадает с прогоном одного момента выше.

Ограничение, которое бэктест печатает сам: funding, стакана и глобального
контекста в серии нет, поэтому полнота данных там всегда ≈30% (артефакт
синтетического бандла, а не рынка) — порог публикации `CONFIDENCE_MIN=0.45` к
ней не применяется, сравниваются только рыночные критерии (качество, R:R,
риск, число целей). Таймфреймы движка пересобираются из этой же истории: на
300 свечах 15m получается 1h: 76 баров, а 4h: 20 баров — макро-ТФ короче 40
баров бэктестер отбрасывает, и это видно в шапке отчёта.

## Качество входа (`v3/analysis/levels.py`)

Три правила, добавленные после бэктеста на реальных свечах (см.
`docs/AUDIT.md`, раунд 8-9; источники — freqtrade/jesse и практика по стопам в
`docs/IMPROVEMENTS_RESEARCH.md`):

1. **Стоп за уровнем.** Стоп, поставленный ровно на support/resistance,
   выбивают сбором ликвидности, поэтому он относится за уровень на
   `ATR_STOP_BUFFER` (0.25×ATR). Базовая дистанция — `ATR_SL_MULTIPLIER`
   (2.2×ATR): узкий стоп выбивается обычным шумом.
2. **Не догонять рынок.** Если цена уже ушла от VWAP в сторону сделки дальше
   `ENTRY_MAX_EXTENSION_ATR` (2.0×ATR), направление откатывается в WAIT, а
   причина видна в карточке: «движение в основном пройдено, ждём откат».
3. **Пауза после серии стопов.** `ALERT_STOPOUT_GUARD` (2) стопа подряд по
   монете — авто-сигнал по ней гасится на `ALERT_STOPOUT_PAUSE_HOURS` (6 ч);
   сетап сохраняется в базу и остаётся в разделах списков.

Эффект на реальной серии OKX (299 баров 15m): profit factor 0.345 → 2.662,
матожидание −0.655R → +1.024R, просадка 4.36R → 1.23R. Сделок всего две —
это регрессионный снимок, а не статистика; проверено в
`v3/tests/test_entry_quality.py` (A/B старых и новых порогов).

## Observability (`v3/observability.py`)

`/health` and `/api/v3/status` expose a `HealthSnapshot`: mode, data_ok,
analyses, scans, avg latency, last error. `RuntimeMetrics` is thread-safe and
local (no external telemetry required); it is meant to be exported to any
monitoring stack later.

## Telegram

Read-only v3 bot (never executes orders). The bot is **closed by default**:
set `TELEGRAM_ALLOWED_USER_IDS` (comma-separated ids; fallback numeric
`TELEGRAM_ADMIN_CHAT_ID`) — with an empty allow-list every user is rejected.

```bash
python -m v3 bot            # needs TELEGRAM_BOT_TOKEN / TELEGRAM_TOKEN
python -m v3 watch          # background watch + scan + TP/SL outcome tracking
python -m v3 watch BTCUSDT,ETHUSDT
```

Interactive UI (inline keyboards, pagination, edit-in-place):

* 🔎 СКАНИРОВАТЬ РЫНОК / 🧠 АНАЛИЗ РЫНКА — Stage1+Stage2 scan
* 🔥 ЛУЧШИЕ LONG / 🔻 ЛУЧШИЕ SHORT / ⭐ ТОП ВОЗМОЖНОСТИ — filtered setups
* 🔍 АНАЛИЗ МОНЕТЫ — coin picker + full card (`🔄 ОБНОВИТЬ`, `📈 PRO`)
* 🔔 АВТО-СИГНАЛЫ — статус фонового поиска, пороги, пауза, «Проверить сейчас»
* 📊 МОЙ РЫНОК — market overview (BTC/ETH/global/F&G/movers)
* ⚙️ НАСТРОЙКИ — per-user mode/deposit/risk (SQLite)
* 📚 ПОМОЩЬ — glossary (RSI, ATR, ADX, BOS/CHoCH, funding, OI, R:R, ...)

Commands still work: `/help`, `/status`, `/signal BTCUSDT`,
`/signal BTCUSDT pro`, `/scan`, `/scan pro`, `/market`, `/alerts`,
`/walkforward BTCUSDT [15m]`. `/start` показывает приветствие: что умеет бот,
как читать его три метрики и с чего начать. Rendering lives in `v3/report.py`,
`v3/tg/render.py`; keyboards in `v3/tg/keyboards.py`; per-user settings in
`v3/tg/settings.py`.

## API

See module docstring / Swagger at `/docs`.

## Configuration

Root `.env` and `v3/.env.example` are both read. Key variables:

`MARKET_DATA_MODE`, `TIMEFRAMES`, `ENTRY_TF`, `ANALYSIS_BARS`,
`SCAN_MIN_TURNOVER_USD`, `SCAN_MIN_VOLUME_USD`, `SCAN_TOP`, `SCAN_LIMIT`,
`WATCHLIST_SYMBOLS`, `WATCHER_SCAN_UNIVERSE`, `SCAN_EXCLUDE_EXHAUSTED`,
`EMERGENCE_MAX_TRIGGER_ATR`, `EMERGENCE_MIN_ROOM_PCT`,
`MAX_DATA_AGE_SECONDS`, `BACKTEST_FUNDING_RATE`,
`AI_ENABLED`, `OPENAI_API_KEY`,
`OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`, `V3_API_TOKEN`, `QUALITY_MIN`,
`CONFIDENCE_MIN`,
`MIN_RISK_REWARD`, `MAX_RISK_SCORE_TO_ENTER`, `ATR_SL_MULTIPLIER`,
`ATR_TP_MULTIPLIER`, `RISK_PER_TRADE_PCT`, `MAX_POSITION_PCT`,
`MAX_LEVERAGE`, `COOLDOWN_SECONDS`, `S/A/B/C_TIER_MIN`,
`WATCHER_INTERVAL_SECONDS`, `ALERTS_ENABLED`, `ALERT_MIN_QUALITY`,
`ALERT_MIN_BOT_CONFIDENCE`, `ALERT_MIN_DATA_CONFIDENCE`,
`ALERT_MAX_RISK_SCORE`, `ALERT_MIN_RR`, `ALERT_MAX_PER_CYCLE`,
`ALERT_CHAT_IDS`, `BOT_CONFIDENCE_WEIGHTS`, `BOT_CONFIDENCE_HIGH_MIN`.

## Security & principle

* v3 never submits orders; there is **no** live execution path in the unified
  engine.
* API keys are optional and only used by read-only exchange/public endpoints;
  never commit `.env`.
* The AI explanation layer (if added) must not modify market data or override
  the deterministic gate.
* Telegram is closed (allow-list); every published signal passes
  `v3.publisher.sanitize_for_publish` (Telegram/API/watcher).

## Limitations (honest)

* Order flow is a **public-depth/CVD proxy**, not exchange private order flow.
* Funding/OI/liquidation data depends on exchange endpoints and current
  availability; absence is shown as «н/д» and only degrades confidence.
* **Demo mode deleted (round 3)**: no synthetic fallback anywhere. Backtests
  run only on real exchange history downloaded via REST.
* No guarantee of profit. Quality > quantity.
