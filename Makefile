.PHONY: help install test test-cov lint format check run-bot run-api docker-up docker-down clean

help:
	@echo "Available commands:"
	@echo "  make install     - Install development dependencies"
	@echo "  make test        - Run test suite with pytest"
	@echo "  make test-cov    - Run tests with coverage report"
	@echo "  make lint        - Run ruff linter & mypy static type checker"
	@echo "  make format      - Autoformat code with ruff"
	@echo "  make check       - Run all checks (lint + test)"
	@echo "  make run-bot     - Run the Telegram bot runner standalone"
	@echo "  make run-api     - Run FastAPI development server with uvicorn"
	@echo "  make docker-up   - Build and start full stack via docker-compose"
	@echo "  make docker-down - Stop docker-compose services"
	@echo "  make clean       - Remove cache and temporary artifacts"

install:
	pip install -r requirements.txt

test:
	PYTHONPATH=. pytest -v

test-cov:
	PYTHONPATH=. pytest --cov=src --cov-report=term-missing tests/

lint:
	ruff check .
	mypy src --explicit-package-bases

format:
	ruff format .

check: lint test

run-bot:
	python -m src.bot.runner

run-api:
	uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

docker-up:
	docker-compose up -d --build

docker-down:
	docker-compose down

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
