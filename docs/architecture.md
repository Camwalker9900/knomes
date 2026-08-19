# Architecture

Knomes is a property-condition and failed-transaction ledger for Houston / Harris County. It is an **event ledger, not a flattened property table**: every public statement about a house is a `ledger_event` that traces back to a government source record, a reviewed submission, or an explicit system inference — never to nothing.

Related docs: [data-model.md](data-model.md) · [data-sources.md](data-sources.md) · [ingestion.md](ingestion.md) · [verification-model.md](verification-model.md) · [deployment.md](deployment.md)

## Monorepo layout

```
knomes/
├── apps/
│   ├── api/            # FastAPI + SQLAlchemy 2 backend (Python 3.12, uv-managed)
│   │   ├── app/        # main.py, core/, models/, enums.py, lib/, api/v1/,
│   │   │               # schemas/, services/, ingestion/, seed/
│   │   ├── alembic/    # migrations (0001 creates extensions + all 16 tables)
│   │   └── tests/
│   └── web/            # Next.js App Router frontend (TS strict, Tailwind)
├── packages/shared/    # shared API contract types (TS) + JSON fixtures
├── data/               # raw/ + staging/ (gitignored), fixtures/ (committed samples)
├── scripts/            # fetch_hcad_sample.py, wait_for_db.py
├── logo/  docs/  plan/  infrastructure/
├── docker-compose.yml  Makefile  .env.example  .github/workflows/ci.yml
```

## Request flow

```
Browser ──▶ Next.js (apps/web, :3000)
              │  src/lib/api.ts (typed client, NEXT_PUBLIC_API_URL)
              ▼
          FastAPI (apps/api, :8000)  /api/v1/properties/…
              │  routers → services (search, timeline) → SQLAlchemy 2 (sync, psycopg)
              ▼
          PostgreSQL 16 + PostGIS + pg_trgm + unaccent (:5432)
```

The read API is anonymous and public. Only events with `visibility = PUBLIC` and `retracted_at IS NULL` are ever served on timelines. Search uses a fixed ladder: exact normalized address → HCAD account ID → trigram similarity (details in [data-model.md](data-model.md)).

## Data flow: RAW → STAGING → CANONICAL → LEDGER

```
 external source (HCAD bulk file, Houston CKAN API, …)
        │ fetch()                       SourceAdapter (app/ingestion/base.py)
        ▼
 RAW        immutable snapshot → data/raw/ + object storage, checksummed,
        │   recorded on source_sync_runs (reproducibility)
        │ parse()  — streaming iterator, never whole-file-in-RAM
        ▼
 STAGING    per-source staging (e.g. hcad_staging via psycopg COPY);
        │   rejected rows are counted, not fatal
        │ normalize()  — NormalizedRecord + deterministic address normalizer
        ▼
 CANONICAL  source_records (unique on source_name/source_record_id/content_hash),
        │   record_property_matches via the matching ladder;
        │   unmatched records go to a review queue — never silently attached
        │ reconcile  — matched records only
        ▼
 LEDGER     ledger_events with event_type, verification_level, confidence,
            provenance FK to source_records; property timelines are read views
```

Every stage is idempotent: re-running an import creates zero duplicate source records or events (content-hash dedup plus the ledger dedup key).

## Tech choices and rationale

| Choice | Why |
|---|---|
| PostgreSQL 16 + PostGIS + pg_trgm + unaccent | One database does relational ledger, parcel geometry (identity fallback), and fuzzy address search. No separate search engine needed at MVP scale. |
| SQLAlchemy 2 typed ORM, sync engine | Simple, typed (`Mapped`/`mapped_column`), easy to test; ingestion is batch-oriented so async DB adds nothing. |
| FastAPI + Pydantic v2 | Typed response schemas *are* the frontend contract (mirrored in `packages/shared`). |
| Redis + RQ | Background jobs (source syncs, future upload processing) without Kafka. A dedicated `worker` container runs `rq worker`. Explicitly no Kafka, no Kubernetes. |
| MinIO (dev) / S3 (prod) | Object storage for raw snapshots and uploaded evidence. **Documents never live in PostgreSQL** — `evidence.storage_key` and snapshot paths point at object storage; the database holds metadata and hashes only. |
| Next.js App Router, TS strict, Tailwind | Server-rendered property pages, typed API client, brand theme (navy `#032042`, flame `#ED5621`, steel `#15537F`). |
| Alembic | Migration-backed schema changes only; autogenerate ignores PostGIS-owned tables. |

## Background jobs

Redis (`redis://localhost:6379/0`) backs RQ. The `worker` service in docker-compose runs the same API image with `rq worker`. Source syncs can run inline (`make import-hcad`, `make import-houston-code`) or be enqueued; both paths go through `app/ingestion/runner.run_sync`, which writes a `source_sync_runs` row per run.

## Services (docker-compose, project `knomes`)

db :5432 · redis :6379 · minio :9000 (console :9001) · api :8000 · worker · web :3000. `make dev` boots all six; see [deployment.md](deployment.md) for the target production shape.
