.PHONY: help install test lint check format run run-api run-scan analyze spectrum plan backtest clean \
	v2-scan v2-analyze v2-watch v2-serve v2-bot v2-status v2-test v2-check \
	v3-signal v3-scan v3-backtest v3-walkforward v3-calibrate v3-serve v3-bot v3-watch v3-status v3-test v3-check

help:
	@echo "HYPE Advisor — аналитический крипто-советник"
	@echo ""
	@echo "  make install     - установить зависимости"
	@echo "  make test        - запустить тесты"
	@echo "  make lint        - ruff"
	@echo "  make check       - lint + тесты"
	@echo "  make run         - полный режим (дашборд + сканер + наблюдение + Telegram)"
	@echo "  make run-api     - только веб-дашборд (порт 8000)"
	@echo "  make run-scan    - разовый скан скрытых монет"
	@echo "  make analyze SYM=SOLUSDT - разовый анализ монеты"
	@echo "  make spectrum SYM=SOLUSDT - полный спектральный анализ"
	@echo "  make plan SYM=SOLUSDT DEPOSIT=500 - карточка сделки для новичка"
	@echo "  make backtest SYM=BTCUSDT DAYS=30 TF=1h - прогон советника по истории"
	@echo ""
	@echo "  ── v2 (новая архитектура: трёхуровневый сканер + скам-фильтр) ──"
	@echo "  make v2-scan   - трёхуровневый скан рынка"
	@echo "  make v2-analyze SYM=AURORA - полный разбор монеты"
	@echo "  make v2-watch  - фоновый скан по расписанию"
	@echo "  make v2-serve  - HTTP-API и дашборд (порт 8100)"
	@echo "  make v2-bot    - Telegram-ассистент"
	@echo "  make v2-status - активные фильтры и метрики"
	@echo "  make v2-test   - тесты v2"
	@echo "  make v2-check  - ruff + тесты v2"
	@echo ""
	@echo "  ── v3 (futures signal intelligence: scanner + walk-forward + AI) ──"
	@echo "  make v3-signal SYM=BTCUSDT MODE=pro - разовый сигнал USDT-perp"
	@echo "  make v3-scan MODE=pro            - скан вселенной USDT-perp"
	@echo "  make v3-backtest SYM=BTCUSDT TF=15m BARS=2000 - бэктест"
	@echo "  make v3-walkforward SYM=BTCUSDT TF=15m BARS=5000 FOLDS=5 - walk-forward"
	@echo "  make v3-calibrate SYMS=BTCUSDT,ETHUSDT TF=15m BARS=2000 - калибровка порогов на выборке"
	@echo "  make v3-serve PORT=8400          - FastAPI-стенд v3"
	@echo "  make v3-bot                     - Telegram-бот v3"
	@echo "  make v3-watch SYMS=BTCUSDT,ETHUSDT - фоновый watcher v3"
	@echo "  make v3-status                  - сохранённые v3-сигналы и health"
	@echo "  make v3-test                    - тесты v3"
	@echo "  make v3-check                   - ruff + тесты v3"
	@echo "  make clean       - очистить кэши"

install:
	pip install -r requirements.txt

test:
	pytest tests/ v2/tests v3/tests -q

lint:
	ruff check .

check: lint test

run:
	python main.py

run-api:
	python main.py api

run-scan:
	python main.py scan

analyze:
	python main.py analyze $(SYM)

spectrum:
	python main.py spectrum $(SYM)

plan:
	python main.py plan $(SYM) --deposit $(or $(DEPOSIT),500)

backtest:
	python main.py backtest $(or $(SYM),BTCUSDT) --days $(or $(DAYS),30) --tf $(or $(TF),1h) --step $(or $(STEP),1)

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +

# ── v2: новая архитектура (каталог v2/) ──────────────────────────
v2-scan:
	python -m v2 scan

v2-analyze:
	python -m v2 analyze $(or $(SYM),$(Q),AURORA) $(if $(DEPOSIT),--deposit $(DEPOSIT),)

v2-watch:
	python -m v2 watch

v2-serve:
	python -m v2 serve

v2-bot:
	python -m v2 bot

v2-status:
	python -m v2 status

v2-test:
	pytest v2/tests -q

v2-check:
	ruff check v2
	pytest v2/tests -q

# ── v3: futures signal intelligence (USDT perpetual) ──────────
v3-signal:
	python -m v3 signal $(SYM) --mode $(or $(MODE),beginner)

v3-scan:
	python -m v3 scan --mode $(or $(MODE),beginner)

v3-backtest:
	python -m v3 backtest $(or $(SYM),BTCUSDT) --tf $(or $(TF),15m) --bars $(or $(BARS),1000) --warmup $(or $(WARMUP),120)

v3-walkforward:
	python -m v3 walkforward $(or $(SYM),BTCUSDT) --tf $(or $(TF),15m) --bars $(or $(BARS),5000) --folds $(or $(FOLDS),5)

v3-calibrate:
	python -m v3 calibrate $(or $(SYMS),BTCUSDT,ETHUSDT,SOLUSDT) --tf $(or $(TF),15m) --bars $(or $(BARS),2000) --warmup $(or $(WARMUP),120)

v3-serve:
	python -m v3 serve --host $(or $(HOST),0.0.0.0) --port $(or $(PORT),8400)

v3-bot:
	python -m v3 bot

v3-status:
	python -m v3 status

v3-test:
	pytest v3/tests -q

v3-check:
	ruff check v3
	pytest v3/tests -q

v3-watch:
	python -m v3 watch $(if $(SYMS),$(SYMS),) 
