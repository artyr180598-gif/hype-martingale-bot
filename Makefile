.PHONY: help install test lint check format run run-api run-scan analyze clean

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
	@echo "  make clean       - очистить кэши"

install:
	pip install -r requirements.txt

test:
	pytest tests/ -q

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

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
