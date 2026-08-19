# Knomes

![Knomes gnome icon](logo/knomes-icon.svg)

**Know your home.** A verified property-condition and failed-transaction
ledger for Houston / Harris County. Knomes records what actually happened to a
house — permits, inspections, condition findings, repairs, code violations,
and transactions that fell through — as an append-only event ledger where
every public event carries an explicit verification level and traces back to
its source record. The system never manufactures causality and never uses a
bare "verified".

## Quickstart

```bash
git clone <repo-url> knomes
cd knomes
cp .env.example .env
make dev        # builds and starts db, redis, minio, api, worker, web
make migrate    # alembic upgrade head
make seed       # loads the synthetic demo properties
```

Open <http://localhost:3000> and search **"100 Test Street"** to see the full
demo arc: listing → inspection → HVAC finding → terminated contract → permit →
verified repair → finding resolved.

## Architecture

```
              +-------------------+
  Browser --> |  web  (Next.js)   |  :3000
              +---------+---------+
                        |  REST / JSON  (NEXT_PUBLIC_API_URL)
              +---------v---------+     jobs via Redis     +--------------+
              |  api  (FastAPI)   | ---------------------> | worker (RQ)  |
              |       :8000       |                        +------+-------+
              +---+------+----+---+                               |
                  |      |    |        (worker shares DB, Redis,  |
                  |      |    |         and object storage) ------+
                  v      v    v
   +--------------+  +--------+  +------------------+
   |  PostgreSQL  |  | Redis  |  |  MinIO (S3 dev)  |
   |  + PostGIS   |  | :6379  |  |  :9000 / :9001   |
   |    :5432     |  +--------+  +------------------+
   +--------------+
```

Data flows **RAW → STAGING → CANONICAL → LEDGER** through pluggable
`SourceAdapter`s (HCAD appraisal rolls, Houston code enforcement). Raw
snapshots are retained as received; matching to properties uses the identity
hierarchy HCAD account ID → parcel geometry → normalized address; uncertain
matches go to a review queue and are never silently attached.

## Repository layout

```
apps/
  api/            FastAPI backend (SQLAlchemy 2, Alembic, ingestion, seed)
  web/            Next.js frontend (App Router, TypeScript strict, Tailwind)
packages/
  shared/         Shared API contract types + JSON parity fixtures
data/
  fixtures/       Committed sample data (hcad_sample/, houston_code_sample/)
  raw/, staging/  Gitignored ingestion working dirs
infrastructure/   Deployment shape notes (not deployed yet)
scripts/          One-off helpers (fetch_hcad_sample.py, wait_for_db.py)
logo/             Gnome icon source + vectorized SVGs
docs/             Architecture, data model, sources, verification model, …
plan/             Implementation plan + frozen interface contracts
tests/            Pointer README (backend tests: apps/api/tests, web: apps/web)
```

## Make targets

| Target | What it does |
|---|---|
| `make dev` | `docker compose up -d --build` — start the full dev stack |
| `make stop` | `docker compose down` |
| `make test` | Run backend + web test suites |
| `make test-api` | `cd apps/api && uv run pytest -q` |
| `make test-web` | `cd apps/web && npm test -- --run` |
| `make lint` | ruff + mypy (api), eslint (web) |
| `make format` | ruff format (api), prettier (web) |
| `make migrate` | `alembic upgrade head` |
| `make seed` | Load the synthetic demo dataset (idempotent) |
| `make import-hcad` | Import the HCAD sample fixture |
| `make import-houston-code` | Import the Houston code-enforcement fixture |
| `make reset-db` | Drop + recreate the dev database, re-run migrations |

## Further reading

- [`docs/`](docs/) — architecture, data model, data sources, verification
  model, privacy, ingestion, moderation, deployment.
- [`plan/`](plan/) — the phased implementation plan
  ([`plan/2026-08-19-knomes-mvp-phases-0-3.md`](plan/2026-08-19-knomes-mvp-phases-0-3.md))
  and the frozen cross-module contracts
  ([`plan/CONTRACTS.md`](plan/CONTRACTS.md)).

## Scope guardrails (MVP)

Houston / Harris County only. No scraping of HAR, Zillow, Realtor.com,
Redfin, or any proprietary real-estate site — MLS data arrives later through a
licensed provider interface. No AVMs, scores, chatbots, marketplaces, or
"write a review" features: only evidence-backed events with explicit
verification levels.
