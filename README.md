# HYPE ULTIMATE v4 — Multi-Exchange Crypto Scanner & Signal Intelligence

> **Полностью переписан с нуля** из лучших open-source проектов GitHub. Старый мартингейл-бот удален. Теперь это мега-бот для поиска монет на любых биржах, анализа и советов LONG/SHORT с ценой, ожидаемым скачком, TP/SL.

## 🧬 Откуда взята логика (самые мощные проекты GitHub)

| Проект | Звезд | Что портировано |
|--------|-------|-----------------|
| **freqtrade/freqtrade** | 54.7k | Архитектура, риск-менеджмент, ROI, стоп-лосс, бэктест, мульти-биржа через CCXT |
| **CryptoMarius/CryptoScanBot** | — | **STOBB, SBM, JUMP** детекторы, мультибиржа Binance/Bybit/MEXC/KuCoin/OKX, скан вселенной |
| **Haehnchen/crypto-trading-bot** | — | Multi-pair в одном инстансе, Web UI, LONG/SHORT, Telegram/Slack |
| **chibyk71/crypto-scanner** | — | Production-grade TS сканер, multi-timeframe, scoring, ML, backtest suite |
| **samshoaib123/Trading_Signal_Bot** | — | ATR TP/SL, confidence 1-3, position sizing, дедупликация |
| **OfficialGIGA/crypto-signal-scanner** | — | BTC dominance, корреляции, AI summaries, smart alerts dedupe |
| **python-telegramBot/crypto-liquidity-ai-trading-bot** | — | Liquidity walls, gaps, sweep detection, orderbook imbalance |

Все идеи объединены в **единый движок ULTIMATE**.

---

## 🚀 Что умеет бот (задача из ТЗ)

### 1. Находить монеты на любых биржах
- Поддерживает **7 бирж**: Binance, Bybit, OKX, MEXC, KuCoin, Gate, Bitget через CCXT
- `build_universe()` агрегирует тикеры, сортирует по turnover, фильтрует по ликвидности
- Primary + fallback: если Bybit недоступен → Binance → OKX → MEXC
- Сканит **300 монет** за цикл, анализирует топ 25 глубоко

### 2. Делать анализ — полный тех.анализ
**Тренд:** EMA 9/20/50/200, SMA, SuperTrend, ADX, PSAR, Ichimoku  
**Моментум:** RSI 7/14/21, Stochastic K/D, StochRSI, MFI, Williams %R, CCI, MACD, AO, дивергенции  
**Волатильность:** ATR, Bollinger Bands, Keltner Channel, Donchian, Squeeze (BB inside KC)  
**Объем:** OBV, CVD, VWAP, VWMA, Volume Z, RVOL, CMF, spike detection  
**Структура:** Swing highs/lows, HH/HL/LH/LL, BOS/CHoCH, поддержка/сопротивление, Fibonacci  
**Деривативы:** Funding rate, OI, orderbook depth, imbalance, liquidation walls/sweeps

### 3. Давать совет LONG / SHORT
- **11 стратегий голосуют** (как в Confluence Terminal):
  1. EMA crossover 9/21
  2. 200 EMA trend filter
  3. RSI
  4. MACD
  5. Bollinger %B
  6. Donchian breakout (ADX gated)
  7. RSI-2 mean reversion
  8. Stochastic
  9. VWAP
  10. Consecutive candles
  11. RSI divergence
- Bias = LONG если long_score > short_score + 12, иначе SHORT, иначе NEUTRAL → NO TRADE

### 4. Писать цену, ожидаемый скачок, TP/SL

Пример карточки:

```
🟢 LONG BTCUSDT — BYBIT
💎 Оценка сетапа: 84/100 (S)

🎯 УВЕРЕННОСТЬ БОТА: 81% — высокая
████████░░ 81 из 100

💰 Цена входа: 67234.50
📍 Зона входа: 67000 — 67450

🛑 Стоп-лосс: 65800 (-2.13%) | 4/10
🎯 Тейк-профиты:
  TP1 68500 (+1.88%) — закрыть 50%
  TP2 69800 (+3.81%) — закрыть 30%
  TP3 71200 (+5.90%) — закрыть 20%

📈 R:R = 1:2.8
🚀 Ожидаемый скачок: +3.2% → 69380 (ATR 2.2 + measured move)

🏛 Режим: TRENDING_UP
⚡ Фаза: TRIGGERED (heat 72)
📚 Стакан: imbalance 0.68 — BULLISH

🔍 Детекторы:
  • SBM LONG — Stoch K=18 D=19 BB%=0.12 RSI=34 + EMA20>50>200 + PSAR bullish
  • JUMP LONG — +3.5% price + 2.8x volume

💡 Почему этот сигнал:
  • Сильный консенсус 32% — 11 стратегий согласны
  • SBM сигнал — STOBB + MA alignment
  • Плотный стакан 78% — ликвидность есть
```

### 5. Все грамотно настроено
- **Risk engine:** ATR SL 2.2x, TP1 1R, TP2 2R, TP3 3.2R, позиция по % риска, плечо по волатильности, ликвидация
- **Quality scoring:** S/A/B/C, 0-100, учитывает консенсус, режим, стакан, R:R, STOBB/SBM/JUMP, early impulse
- **Bot confidence:** 6 независимых анализов (quality 30%, data 15%, trend 20%, confirm 15%, risk 10%, impulse 10%)
- **Early impulse:** heat scoring, RVOL, squeeze release, consolidation, room to move, фазы EARLY/TRIGGERED/EXHAUSTED
- **Orderbook:** imbalance, стены >$100k, depth, spread, sweep detection (stop hunt)
- **NO TRADE gate:** если качество < min, R:R < 1.8, риск > 7, данные stale, цена ушла от VWAP >2.2 ATR → NO TRADE
- **Авто-сигналы:** фоновый watcher каждые 180с, пишет только если quality≥75, conf≥68%, R:R≥1.8

---

## 📦 Архитектура

```
Market Data (CCXT 7 exchanges)
  → Ticker + Orderbook + Klines (5m,15m,1h,4h,1d) + Funding
  → TTL Cache + Failover Bybit→Binance→OKX→MEXC
  → Universe Builder (turnover filter)
  → Heat Scoring (RVOL, squeeze, consolidation, room)
  → Early Impulse (EARLY/TRIGGERED/EXHAUSTED)
  → Indicators (trend, momentum, volatility, volume, structure)
  → Confluence (11 strategies voting)
  → Regime (TRENDING/RANGING/BREAKOUT/HIGH_VOL)
  → Orderbook Analysis (imbalance, walls, sweep)
  → STOBB/SBM/JUMP detectors (from CryptoScanBot)
  → Risk Engine (ATR SL/TP, RR, leverage, liquidation)
  → Quality Scoring (S/A/B/C)
  → Bot Confidence (6 components)
  → Expected Move (ATR + measured move)
  → Signal Generator (LONG/SHORT + entry zone + 3 TP + SL)
  → SQLite Store
  → Telegram + REST API + Watcher
```

---

## 🔧 Быстрый старт

```bash
git clone https://github.com/artyr180598-gif/hype-martingale-bot
cd hype-martingale-bot

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # заполни TELEGRAM_BOT_TOKEN и TELEGRAM_ALLOWED_USER_IDS

# Проверка
python -m src.hype.cli status

# Анализ одной монеты
python -m src.hype.cli signal BTCUSDT --mode pro

# Скан рынка
python -m src.hype.cli scan --limit 200 --top 15

# Обзор рынка
python -m src.hype.cli market

# Полный daemon: API + watcher + Telegram
python -m src.hype.cli daemon
# или
python main.py daemon
```

### Docker

```bash
docker-compose up --build
# API на :8400, health на /health
```

---

## 📡 REST API (порт 8400)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | health, версия, биржи |
| GET | `/api/v1/universe?limit=100` | вселенная монет |
| GET | `/api/v1/signal/{symbol}` | полный сигнал по монете |
| POST | `/api/v1/scan` | скан рынка `{"limit":250,"top":20,"min_quality":55}` |
| GET | `/api/v1/top?direction=LONG&limit=20` | топ из БД |
| GET | `/api/v1/history/{symbol}?limit=50` | история сигналов |
| GET | `/api/v1/market` | BTC/ETH + gainers |
| GET | `/api/v1/alerts` | статус авто-сигналов |

Swagger: `/docs`

---

## 🤖 Telegram

| Кнопка | Что делает |
|--------|------------|
| 🔎 Сканировать рынок | Stage1 heat → Stage2 глубокий анализ |
| 🔥 Лучшие LONG / 🔻 Лучшие SHORT | топ по направлению из БД |
| ⭐ Топ возможности | без фильтра направления |
| 🔍 Анализ монеты | ввод символа → полный разбор |
| 🔔 Авто-сигналы | статус, пороги, пауза, проверить сейчас |
| 📊 Мой рынок | BTC/ETH + топ рост |
| ⚙️ Настройки | режим beginner/pro, депозит, риск |
| 📚 Помощь | глоссарий |

После сигнала: `🔄 Обновить`, `📈 PRO` (полный разбор с консенсусом и структурой), `📊 График`.

Команды: `/start`, `/scan`, `/signal BTCUSDT`, `/market`, `/help`

---

## ⚙️ Переменные окружения (ключевые)

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `EXCHANGES` | `binance,bybit,okx,mexc,kucoin,gate,bitget` | список бирж |
| `PRIMARY_EXCHANGE` | `bybit` | основная |
| `SCAN_TOP` | `25` | глубокий анализ топ-N |
| `SCAN_LIMIT` | `300` | размер вселенной |
| `TIMEFRAMES` | `5m,15m,1h,4h,1d` | таймфреймы |
| `STOBB_STOCH_K_MAX` | `25` | порог STOBB |
| `JUMP_PRICE_PCT_MIN` | `3.0` | мин % для JUMP |
| `ATR_SL_MULTIPLIER` | `2.2` | стоп в ATR |
| `MIN_RISK_REWARD` | `1.8` | мин R:R |
| `ALERT_MIN_QUALITY` | `75` | порог авто-сигнала |
| `ALERT_MIN_BOT_CONFIDENCE` | `68` | мин уверенность % |
| `WATCHER_INTERVAL_SECONDS` | `180` | интервал скана |

Полный список — `.env.example`

---

## 🧪 Что внутри детекторов

### STOBB (из CryptoScanBot)
Oversold на Stochastic + Bollinger:
- LONG: Stoch K<25, D<25, BB %B <0.15, RSI<40
- SHORT: Stoch K>75, D>75, BB %B >0.85, RSI>60

### SBM (STOBB + MA + PSAR)
- STOBB + EMA20>50>200 + PSAR bullish + price>EMA20 → LONG SBM (сильнее STOBB)
- Инверс для SHORT

### JUMP
Резкий рост цены + объема:
- Price change ≥3% за 6 баров + Volume ≥2.5x avg + RVOL high

### Liquidity
- Imbalance >0.65 bullish, <0.35 bearish
- Walls >$100k — поддержка/сопротивление
- Sweep: wick за recent high/low + возврат → stop hunt

### Early Impulse
- Heat 0-100 из RVOL, squeeze release, consolidation, room, volume_z, ATR%, RSI mid, MFI, ADX
- Фазы: EARLY (база просыпается), TRIGGERED (пробой подтвержден), EXHAUSTED (выжато), WATCH

---

## 🔒 Безопасность

- Read-only: нет `create_order`/`place_order` — только аналитика, исполнение отделено
- Секреты только из env/.env, `.env` в `.gitignore`
- Telegram закрыт allow-list
- API закрыт `API_TOKEN` опционально
- AI не может менять direction/levels/score

---

## 📚 Дисклеймер

Любой анализ/сигнал — **статистическая оценка, не гарантия результата**. Quality ≈ качество сетапа, **не вероятность прибыли**. Криптофьючерсы высокорискованны; не используйте плечо, которое не можете позволить потерять.

---

## 🛠 Сборка

`v4.0.0 · ULTIMATE v4: Multi-exchange + STOBB/SBM/JUMP + Liquidity + Confluence`

Портировано из:
- freqtrade (54.7k stars) — лучший торговый бот GitHub
- CryptoScanBot — лучший сканер STOBB/SBM/JUMP
- + 5 других топ-проектов

Старый мартингейл полностью удален. Новый движок — с нуля, production-ready.
