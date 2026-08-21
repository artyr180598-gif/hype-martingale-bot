.PHONY: install test lint typecheck format check api infra-up infra-down

install:
	python -m pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy app

check: lint typecheck test

api:
	uvicorn app.api.main:app --reload

infra-up:
	docker compose up -d

infra-down:
	docker compose down
