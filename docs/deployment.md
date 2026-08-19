# Deployment

> **Current status: not yet deployed.** The MVP runs locally via Docker Compose only (`make dev`). This document records the target production shape so nothing in the codebase paints us away from it.

Related docs: [architecture.md](architecture.md) · [ingestion.md](ingestion.md)

## Target shape

Deliberately boring. **No Kubernetes, no Kafka.**

| Component | Local (today) | Production (target) |
|---|---|---|
| Web | `web` container, `npm run dev`, :3000 | Next.js on a managed Node host (or static+SSR platform) |
| API | `api` container, uvicorn --reload, :8000 | FastAPI behind uvicorn on a small VM/PaaS instance |
| Worker | `worker` container, `rq worker` | Same API image, one RQ worker process |
| Database | `postgis/postgis:16-3.4`, :5432 | **Managed PostgreSQL with PostGIS** (+ pg_trgm, unaccent) |
| Object storage | MinIO, :9000 (console :9001) | **S3** (or S3-compatible) — raw snapshots + evidence documents; never in Postgres |
| Queue | `redis:7`, :6379 | Managed Redis |

Schema changes ship only through Alembic (`make migrate`); CI runs an `alembic upgrade head` smoke test against a PostGIS service container.

## Environment variables (from `.env.example`)

| Variable | Dev default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://knomes:knomes@localhost:5432/knomes` | SQLAlchemy sync engine URL |
| `TEST_DATABASE_URL` | `postgresql+psycopg://knomes:knomes@localhost:5432/knomes_test` | pytest database |
| `REDIS_URL` | `redis://localhost:6379/0` | RQ queue backend |
| `S3_ENDPOINT` | `http://localhost:9000` | Object storage endpoint (MinIO dev / S3 prod) |
| `S3_BUCKET` | `knomes-dev` | Snapshot + evidence bucket |
| `S3_ACCESS_KEY` | `minioadmin` | Object storage credential (dev-only value) |
| `S3_SECRET_KEY` | `minioadmin` | Object storage credential (dev-only value) |
| `REPLIERS_API_KEY` | *(empty)* | MLS via Repliers — deferred; unset in Phases 0–3 |
| `ADMIN_EMAIL` | `admin@example.com` | Admin contact |
| `APP_ENV` | `development` | Environment name |
| `APP_SECRET` | `dev-secret-change-me` | App secret — must be rotated for any non-dev environment |
| `HCAD_DOWNLOAD_URL` | *(empty)* | HCAD bulk file URL, set when confirmed |
| `HOUSTON_CKAN_BASE_URL` | `https://data.houstontx.gov` | Houston Open Data CKAN base |
| `HOUSTON_CODE_RESOURCE_ID` | *(empty)* | CKAN resource id for code enforcement |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Web → API base URL |

No secrets are committed; `.env` is gitignored and `.env.example` carries only dev defaults. Settings load via pydantic-settings (`app/core/config.py`, env file `.env`, `extra="ignore"`).

## Local operations

```sh
cp .env.example .env
make dev          # docker compose up -d --build (project name: knomes, six services)
make migrate      # alembic upgrade head (in api container if up, else via uv)
make seed         # synthetic demo properties (100/200/300 Test Street)
make test         # test-api (pytest) + test-web (vitest)
make stop / make reset-db
```

Health: `GET :8000/health` reports `database` / `redis` / `storage` as `ok`/`unavailable` with best-effort, short-timeout checks — the same endpoint a production load balancer would probe.

## Pre-production checklist (when the time comes)

Managed Postgres with PostGIS + pg_trgm + unaccent enabled · S3 bucket private with server-side encryption · rotate `APP_SECRET` and storage credentials · run migrations before app rollout · structured JSON logs shipped from stdout · scheduled `make import-hcad` / `make import-houston-code` runs via the worker, each leaving its `source_sync_runs` audit row ([ingestion.md](ingestion.md)).
