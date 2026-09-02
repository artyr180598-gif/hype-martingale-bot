# HYPE — Crypto Market Intelligence & Trading Analysis Platform

Профессиональная аналитическая платформа для USDT/USDC perpetual futures с
Telegram-интерфейсом. Не «бот с индикаторами», а система:

```
Market Data → Normalization → Scanner → Liquidity/Volume → Technical Analysis
→ Market Structure → Derivatives → Volatility → Signal Engine → Risk Engine
→ Confidence/Quality → AI Explanation → Telegram
```

**Бот не торгует.** Это аналитический сигнальный слой: исполнение ордеров
полностью отделено и здесь не реализовано. Все отчёты — статистическая оценка,
а не гарантия результата.

---

## Возможности

* ⚡ **Ранний отбор «намечающегося движения» (раунд 4)**: сканер больше не
  ловит «уже разогретое» — в heat входят RVOL, выход из сжатия (squeeze
  release), консолидация, близость к экстремуму 24h-диапазона, рост OI при
  спокойной цене, относительная сила vs BTC; анти-chase штраф у вершины/дна
  после большого хода; «⚡ Намечается движение» в карточке — это признак
  *ранжирования*, а не триггер (детерминированный гейт не изменён);
  диверсификация корзины (макс. кандидатов одной «корзины» в Stage 2),
  метка возраста листинга (`fresh` — отдельный режим); фазы `EARLY` /
  `TRIGGERED` / `EXHAUSTED`, проверка давления закрытой свечи и запас до
  границы диапазона; формирующаяся свеча исключается, поэтому RVOL/пробой не
  «перерисовываются» внутри часа. В daemon-режиме этот поиск идёт по всей
  ликвидной вселенной, а не только по `WATCHLIST_SYMBOLS`.
* 🔎 **Интерактивный Telegram UI**: главное меню, «Сканировать рынок»,
  «Лучшие LONG/SHORT», «Топ возможности», «Анализ монеты», «Мой рынок»,
  «Настройки», «Помощь»; пагинация, кнопки «Обновить», «PRO», «Назад».
  **История диалога не затирается**: независимые запросы публикуются новыми
  сообщениями; правится только навигация внутри одного результата
  (пагинация/«PRO»/«🔄 ОБНОВИТЬ»).
* 🔒 **Закрытый бот**: `TELEGRAM_ALLOWED_USER_IDS` (fallback — числовой
  `TELEGRAM_ADMIN_CHAT_ID`); без allow-list доступ закрыт для всех.
* 🧠 **Двухэтапный скан**: Stage 1 — быстрый отсев вселенной по
  turnover/спреду/heat; Stage 2 — глубокий анализ топ-N с полным гейтом.
* 📈 **Multi-timeframe**: `5m,15m,1h,4h,1d` (настраивается через `TIMEFRAMES`),
  конфликт таймфреймов → `NO TRADE` с объяснением.
* 🧪 **Индикаторы в контексте** (не «RSI<30 = BUY»): EMA/SMA, RSI, MACD, ATR,
  ADX, Bollinger, Stochastic, VWAP, volume-z, OBV/CVD, squeeze, SuperTrend.
* 🏗 **Market structure**: HH/HL/LH/LL, BOS/CHoCH (свинги + зигзаг),
  поддержка/сопротивление, инвалидация по структуре. Сценарии: тренд,
  CHoCH-разворот, liquidity sweep, mean-reversion в диапазоне, условный пробой.
* 📉 **Derivatives**: funding (история/тренд), open interest (+ изменение за
  24ч после накопления истории), **реальные ликвидации Bybit v5** (публичный
  WebSocket `liquidation.<SYMBOL>`, один коллектор на процесс; офлайн → «н/д»),
  **Bybit Long/Short account-ratio** (публичный эндпоинт, 300s TTL) и
  **mark/index** (из тикера, 0 доп. запросов); спред/глубина стакана,
  imbalance, slippage-прокси.
* 🧭 **Market regime + BTC/ETH контекст**: TRENDING_UP/DOWN, RANGING,
  HIGH/LOW_VOLATILITY, BREAKOUT/BREAKDOWN, ACCUMULATION/DISTRIBUTION,
  UNCERTAIN; контекст меняет интерпретацию, но не генерирует сигнал сам.
* 🎯 **Entry plan**: entry zone (якорится на поддержку/VWAP), SL по ATR и
  структуре, TP1/TP2/TP3, R:R, invalidation, risk brief (риск $, позиция,
  плечо ≤ по волатильности, ликвидация).
* ⛔ **NO TRADE — полноценная функция**: система не обязана выдавать сигнал.
* 🕐 **Data freshness**: timestamp в каждом отчёте; устаревшие данные →
  `⚠️ DATA STALE` и блокировка сигнала.
* 📚 **Глоссарий**: кнопка «Что это?» объясняет RSI, ATR, ADX, BOS/CHoCH,
  funding, OI, R:R, VWAP, regime простым языком.
* ⚙️ **Настройки пользователя**: режим beginner/pro, депозит, риск на сделку
  (хранятся в SQLite, ограничены безопасными границами).
* 🧪 **Backtesting**: fees/slippage/funding, без look-ahead, метрики + разбивка
  по направлению и market regime; walk-forward и read-only калибровка.
* 🤖 **AI-слой только для объяснений**: rule-based по умолчанию, опциональный
  OpenAI; не может изменить direction/levels/score.
* ✅ **Надёжность и только реальные данные**: TTL-кэши (tickers/klines/стакан/
  funding/ликвидции), параллельный сбор bundle, ретраи с экспоненциальным
  backoff + `Retry-After` на 429, failover Bybit → Binance → MEXC (без
  демо-фолбэка: `MARKET_DATA_MODE=demo` удалён), graceful degradation; без
  данных — «Нет реальных данных», retry и диагностика по каждому источнику.

---

## Быстрый старт

```bash
git clone https://github.com/artyr180598-gif/hype-martingale-bot
cd hype-martingale-bot

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # заполните TELEGRAM_* при необходимости

# самодиагностика источников (офлайн покажет «недоступен», без подмены данными)
python -m v3 pulse

# live/auto (публичные данные Bybit, ключи не нужны)
python -m v3 signal SOLUSDT --mode pro
python -m v3 scan --limit 250 --top 12
python -m v3 market

# полный движок: API + watcher + Telegram
python -m v3 daemon            # (это и команда по умолчанию)
```

> **Telegram-доступ**: задайте `TELEGRAM_BOT_TOKEN` и
> `TELEGRAM_ALLOWED_USER_IDS=YOUR_TELEGRAM_USER_ID`. Узнать свой ID можно у
> @userinfobot. Без allow-list бот отвечает «⛔ НЕТ ДОСТУПА» всем.

## Как бот ищет импульс — объяснение для новичка

Главная ошибка обычного сканера: он сортирует монеты по росту за 24 часа.
Так в топ попадает то, что уже выросло, а не то, где движение только
формируется. HYPE теперь разделяет **готовность импульса** и уже случившийся
ход:

1. **Stage 1** отбрасывает неликвидные пары и смотрит всю вселенную USDT-perp.
2. Для лучших кандидатов берутся реальные свечи `1h`. Последняя формирующаяся
   свеча не используется: её объём и цена ещё меняются.
3. Проверяются независимые признаки: объём относительно прошлых свечей (RVOL),
   ускорение объёма, сжатие ATR/Bollinger, узкая база, закрытие около края
   свечи, пробой предыдущего коридора, запас до границы 24h-диапазона,
   относительная сила к BTC и изменение OI.
4. Кандидат получает понятную фазу:
   * **EARLY** — база просыпается, но пробой ещё не убежал;
   * **TRIGGERED** — закрытая свеча подтвердила первый выход из коридора;
   * **EXHAUSTED** — цена уже слишком далеко после сильного хода; такой
     кандидат не показывается в блоке ранних возможностей.
5. Только после этого Stage 2 запускает полный multi-timeframe анализ и тот же
   строгий гейт: спред, стакан, свежесть, конфликт таймфреймов, риск, R:R и
   качество. Метка «намечается» **не является приказом входить**.

Ориентир по чтению результата: **EARLY** — добавить в наблюдение и ждать
подтверждения; **TRIGGERED** — проверять уровни и входить только по готовому
сетапу движка; **EXHAUSTED** — не догонять. `S/A/B/C` — качество комбинации
факторов, а не вероятность прибыли. Если чистого сетапа нет, правильный ответ
бота — `NO TRADE`.

В фоновом `daemon` поиск всей вселенной включён параметром
`WATCHER_SCAN_UNIVERSE=true`. Точечная команда `python -m v3 watch
BTCUSDT,ETHUSDT` оставляет старый режим наблюдения выбранных символов.

## Telegram — что нажимать

| Кнопка | Что делает |
|---|---|
| 🔎 СКАНИРОВАТЬ РЫНОК | Stage 1 скан вселенной → Stage 2 глубокий анализ |
| 🔥 ЛУЧШИЕ LONG / 🔻 ЛУЧШИЕ SHORT | топ сетапы по направлению (quality-фильтр) |
| ⭐ ТОП ВОЗМОЖНОСТИ | лучшие сетапы без фильтра направления |
| 🔍 АНАЛИЗ МОНЕТЫ | выбор/ввод символа → полный отчёт |
| 📊 МОЙ РЫНОК | BTC/ETH/глобальный/страх&жадность/гайнеры |
| ⚙️ НАСТРОЙКИ | режим отчёта, депозит, риск на сделку |
| 📚 ПОМОЩЬ | глоссарий «что это?» |

После анализа: `🔄 ОБНОВИТЬ` (свежие данные), `📈 PRO` (полный разбор),
«Назад», «Главная». Команды `/signal`, `/scan`, `/walkforward`, `/status`
работают как раньше.

## CLI

```bash
python -m v3 signal BTCUSDT --mode pro     # полный анализ
python -m v3 scan --mode pro               # скан вселенной
python -m v3 market                        # обзор рынка
python -m v3 backtest BTCUSDT --tf 15m --bars 2000
python -m v3 walkforward BTCUSDT --tf 15m --bars 5000 --folds 5
python -m v3 calibrate BTCUSDT,ETHUSDT,SOLUSDT --tf 15m --bars 2000
python -m v3 status | pulse                # health / самодиагностика
python -m v3 daemon                        # API + watcher + Telegram
```

## REST API (порт 8400)

| Метод | Путь | Что возвращает |
|---|---|---|
| GET | `/health` | health, режим, счётчики |
| GET | `/api/v3/market` | market overview (BTC/ETH/global/movers) |
| GET | `/api/v3/top?direction=LONG&limit=10` | топ сетапы из последнего скана |
| POST | `/api/v3/scan` | авто-скан, `tradable` |
| GET | `/api/v3/signal/{symbol}` | полный сигнал (валидируется перед сохранением) |
| GET | `/api/v3/history/{symbol}` | история сигналов |
| POST | `/api/v3/track` | TP/SL lifecycle по ценам |
| GET | `/api/v3/backtest/{symbol}` | бэктест |
| GET | `/api/v3/walk-forward/{symbol}` | walk-forward |
| GET | `/api/v3/calibrate` | read-only калибровка |
| GET | `/api/v3/explain/{uid}` | score breakdown |
| GET | `/api/v3/glossary/{term}` | объяснение термина |
| GET | `/api/v3/outcomes` | исходы сигналов |

Если задан `V3_API_TOKEN`, тяжёлые эндпоинты требуют заголовок `X-API-Token`.
Swagger: `/docs`.

## Переменные окружения

Полный список — [`v3/.env.example`](v3/.env.example). Ключевые:

| Переменная | По умолчанию | Значение |
|---|---|---|
| `MARKET_DATA_MODE` | `live` | `live` / `auto` (demo **удалён** — ошибка конфигурации при старте) |
| `TELEGRAM_BOT_TOKEN` | пусто | токен бота (алиас `TELEGRAM_TOKEN`) |
| `TELEGRAM_ALLOWED_USER_IDS` | пусто | user ids через запятую — **доступ бота закрыт без него** |
| `TELEGRAM_ADMIN_CHAT_ID` | пусто | уведомления (numeric — fallback allow-list) |
| `TIMEFRAMES` | `5m,15m,1h,4h,1d` | порядок быстрый → медленный |
| `SCAN_TOP` | `20` | сколько кандидатов анализировать глубоко (Stage 2) |
| `SCAN_SHOW_QUALITY_MIN` | `72` | порог показа в строгом «⭐ ТОП» |
| `SCAN_LIST_QUALITY_MIN` | `58` | порог тир-осознанных списков (B/C тоже видны, 0 < x ≤ 72) |
| `WATCHER_SCAN_UNIVERSE` | `true` | daemon сканирует всю ликвидную вселенную; явный `watch SYMS` — точечный режим |
| `SCAN_EXCLUDE_EXHAUSTED` | `true` | не отправлять уже выжатые импульсы в глубокий анализ |
| `EMERGENCE_MAX_TRIGGER_ATR` | `0.75` | максимум расстояния закрытия от пробитой базы в ATR |
| `EMERGENCE_MIN_ROOM_PCT` | `0.15` | минимум запаса до границы диапазона для раннего кандидата |
| `MIN_RISK_REWARD_REVERSAL` | `1.5` | смягчённый R:R только для разворотных сценариев |
| `LIQUIDATIONS_WS_ENABLED` | `true` | реальные ликвидации Bybit WS; недоступно → «н/д» |
| `MAX_DATA_AGE_SECONDS` | `120` | stale → NO TRADE |
| `QUALITY_MIN` | `55` | минимальный quality для сигнала |
| `MIN_RISK_REWARD` | `1.8` | минимальный R:R |
| `V3_API_TOKEN` | пусто | защита тяжёлых endpoint'ов |
| `OPENAI_API_KEY` | пусто | опциональный AI-аннотатор |

## Тесты

```bash
make check   # ruff + pytest
make test    # pytest
```

**72 теста**: анализаторы, сканер, walk-forward, AI reasoning, stale-data
gate, lifecycle, backtest-метрики (+ разбивка regime/direction), калибровка,
Telegram core/авторизация/callback'и/настройки, TTL-кэш, 429 retry,
структурный entry zone, publisher/stale validation, config validation,
Bybit account-ratio endpoint (+ 300s TTL), **инварианты «только реальные
данные»** (`v3/tests/test_realdata.py`: demo у конфигурации/factory удалён,
fail-closed без тикера/свечей/timestamp, WS-ликвидации на фейк-сессии),
**история диалога** (`v3/tests/test_telegram_history.py`: независимые запросы
→ новые сообщения, навигация внутри результата → правка, без `delete`).

## Только реальные данные (политика платформы)

* **ТОЛЬКО** реальные биржи Bybit → Binance → MEXC; `MARKET_DATA_MODE=demo`
  удалён: ошибка конфигурации на старте (и в `v3/config.py`, и в `build_source`).
* Возраст данных — **по биржевым timestamp** (свеча/тикер); без биржевого
  timestamp метрики НЕ публикуются: валидатор блокирует, движок отвечает NO TRADE.
* Нет минимального набора (тикер + свечи) → сообщение «⚠️ Нет реальных данных —
  анализ невозможен» + причины + вердикт по каждому источнику + кнопка
  «🔄 ПОПРОБОВАТЬ СНОВА». Ничего не подставляется вместо недостающих данных.

## Развёртывание (Railway / Docker)

* Startup: `python -m v3 daemon` (или `V3_COMMAND=daemon` через
  [`entrypoint.sh`](entrypoint.sh)); порт — из `PORT` (Railway инжектит сам).
* Healthcheck: `GET /health` (есть в [`Dockerfile`](Dockerfile)).
* Данные SQLite — в `DATA_DIR` (по умолчанию `./data`); для multi-replica
  используйте volume.
* Один процесс: API + watcher + Telegram; SIGTERM → graceful shutdown.
* При старте выполняется `validate_config` — ошибки конфигурации логируются,
  процесс не падает от отсутствия опциональных секретов.

## Безопасность

* Read-only: в проекте нет пути исполнения ордеров.
* Секреты только из env/`.env`; `.env`, `data/`, `*.log` в `.gitignore`.
* Telegram закрыт allow-list; API закрыт `V3_API_TOKEN` (по желанию).
* AI-слой не может изменить direction/levels/score — гейт всегда первичен.
* `v3/validator.py` подключён на всех путях публикации (Telegram, API, watcher).
* См. [`SECURITY.md`](SECURITY.md) и [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Дисклеймер

Любой анализ/сигнал — **статистическая оценка, а не гарантия результата**.
Signal Quality ≈ качество сетапа, **не вероятность прибыли**. Криптофьючерсы
высокорискованны; не используйте плечо, которое не можете позволить потерять.
