.PHONY: help install test lint check format run run-api run-scan analyze spectrum plan backtest clean \
	v2-scan v2-analyze v2-watch v2-serve v2-bot v2-status v2-test v2-check

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
	@echo "  make clean       - очистить кэши"

install:
	pip install -r requirements.txt

test:
	pytest tests/ v2/tests -q

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
