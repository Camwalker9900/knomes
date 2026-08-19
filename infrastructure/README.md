# Infrastructure

**Status: not deployed.** Knomes currently runs only as a local development
stack via `docker-compose.yml` at the repo root (`make dev`). This directory
documents the intended deployment shape (spec §70) so that nothing in the
codebase drifts away from it.

## Target deployment shape (spec §70)

Deliberately boring. No Kubernetes, no Kafka, no service mesh.

| Component | Production shape | Dev equivalent |
|---|---|---|
| Database | Managed PostgreSQL 16 with PostGIS, `pg_trgm`, `unaccent` extensions | `db` compose service (`postgis/postgis:16-3.4`) |
| Object storage | Managed S3-compatible bucket (documents/evidence never live in PostgreSQL) | `minio` compose service |
| Queue | Managed Redis (RQ workers) | `redis` compose service |
| API | FastAPI container (`apps/api`), horizontally scalable, stateless | `api` compose service |
| Worker | Same image as the API, running `rq worker` | `worker` compose service |
| Web | Next.js container (`apps/web`) | `web` compose service |

Principles carried over from the spec:

- Single region, single database; scale reads before sharding anything.
- All schema changes ship as Alembic migrations — no manual DDL in any
  environment.
- Secrets come from the platform's secret manager, never from committed files
  (`.env` is gitignored; `.env.example` holds only dev defaults).
- Raw source snapshots are retained in object storage so every ingestion run
  is reproducible.

When deployment actually happens, the concrete provider choices, runbooks, and
provisioning code will land in this directory. Until then there is nothing
else here on purpose.
