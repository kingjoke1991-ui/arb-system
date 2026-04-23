.PHONY: install lint test run up down schema

install:
	pip install -e ".[dev]"

lint:
	ruff check app tests
	ruff format --check app tests

fmt:
	ruff format app tests
	ruff check --fix app tests

test:
	pytest -q

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

up:
	docker compose up -d --build

down:
	docker compose down

schema:
	python -m app.scripts.apply_schema
