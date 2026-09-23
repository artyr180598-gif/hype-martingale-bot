.PHONY: help install test lint check run serve scan signal market status bot clean

PYTEST := $(shell if [ -x .venv/bin/pytest ]; then echo .venv/bin/pytest; else echo pytest; fi)
RUFF := $(shell if [ -x .venv/bin/ruff ]; then echo .venv/bin/ruff; else echo ruff; fi)

help:
	@echo "HYPE ULTIMATE v4 — Multi-exchange scanner"
	@echo ""
	@echo "  make install       - deps"
	@echo "  make test          - pytest"
	@echo "  make lint          - ruff"
	@echo "  make check         - lint + test"
	@echo "  make run           - daemon API+watcher+Telegram"
	@echo "  make serve         - API only"
	@echo "  make scan          - scan universe"
	@echo "  make signal SYM=BTCUSDT MODE=pro"
	@echo "  make market        - market overview"
	@echo "  make status        - config + recent"
	@echo "  make bot           - Telegram only"
	@echo "  make clean"

install:
	pip install -r requirements.txt

test:
	$(PYTEST) -q

lint:
	$(RUFF) check .

check: lint test

run:
	python -m src.hype.cli daemon --host $(or $(HOST),0.0.0.0) --port $(or $(PORT),8400)

serve:
	python -m src.hype.cli serve --host $(or $(HOST),0.0.0.0) --port $(or $(PORT),8400)

scan:
	python -m src.hype.cli scan --limit $(or $(LIMIT),250) --top $(or $(TOP),20)

signal:
	python -m src.hype.cli signal $(or $(SYM),BTCUSDT) --mode $(or $(MODE),pro)

market:
	python -m src.hype.cli market

status:
	python -m src.hype.cli status

bot:
	python -m src.hype.cli bot

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
