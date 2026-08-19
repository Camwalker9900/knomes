# Knomes — root Makefile (compose project name: knomes, set in docker-compose.yml)

.PHONY: dev stop test test-api test-web lint format migrate seed import-hcad import-houston-code reset-db

dev:
	docker compose up -d --build

stop:
	docker compose down

test: test-api test-web

test-api:
	cd apps/api && uv run pytest -q

test-web:
	cd apps/web && npm test -- --run

lint:
	cd apps/api && uv run ruff check . && uv run mypy app
	cd apps/web && npm run lint

format:
	cd apps/api && uv run ruff format .
	cd apps/web && npx prettier --write .

migrate:
	cd apps/api && uv run alembic upgrade head

seed:
	cd apps/api && uv run python -m app.seed

import-hcad:
	cd apps/api && uv run python -m app.ingestion.hcad.sync --file ../../data/fixtures/hcad_sample/real_acct.txt

import-houston-code:
	cd apps/api && uv run python -m app.ingestion.houston_code.sync --file ../../data/fixtures/houston_code_sample/records.json

reset-db:
	docker compose exec db psql -U knomes -c "DROP DATABASE IF EXISTS knomes WITH (FORCE)" postgres && docker compose exec db psql -U knomes -c "CREATE DATABASE knomes" postgres && $(MAKE) migrate
