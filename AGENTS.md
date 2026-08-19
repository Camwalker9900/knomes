# AGENTS.md — working on Knomes with a coding agent

Knomes ("Know your home") is a **verified property-condition and failed-transaction
ledger** for Houston / Harris County. The product is an evidence-backed event
timeline per property — never a review site, never a score, never an inference
the evidence doesn't support.

## Read these first

- `plan/2026-08-19-knomes-mvp-phases-0-3.md` — the implementation plan (Phases 0–3 built; later phases deferred).
- `plan/CONTRACTS.md` — **frozen cross-module interfaces**: enums, API response shapes, adapter signatures, match-ladder confidences. Code must match it verbatim; change the contract file in the same PR if an interface must move.
- `docs/` — architecture, data model, data sources, verification model, privacy, ingestion, moderation, deployment.

## Non-negotiable invariants (spec §63)

1. **No public claim without provenance.** Every public ledger event traces to a
   `source_records` row (or, later, evidence/submission). Government events carry
   `verification_level=GOVERNMENT_RECORD` and a `source_record_id`.
2. **No causal inference without evidence.** A `Pending → Active` listing change
   may yield `TRANSACTION_TERMINATED` with `termination_reason=UNKNOWN` and
   `verification_level=SYSTEM_INFERRED` — it must **never** become
   `PROPERTY_CONDITION` (or any other cause) without documentation. Public wording:
   *"A previous contract appears not to have closed. Reason not verified."*
3. **Machines propose, humans approve.** The resolution engine only creates
   `resolution_candidates` rows (`REVIEW_REQUIRED`); it never mutates
   `findings.status`. There is no `FIXED` status anywhere.
4. **History is never deleted.** Use `retracted_at`/`retraction_reason`; disputes
   add records, they don't remove them. Audit machine actions in `audit_log`.
5. **Originals are private by default.** Documents live in object storage, never
   in Postgres; only reviewed, derived findings become public.
6. **Never scrape** HAR, Zillow, Realtor.com, Redfin, or any proprietary
   real-estate site. MLS arrives later through the Repliers license behind the
   `ListingProvider` interface. Allowed remote hosts today: `download.hcad.org`
   (manual sample script) and `data.houstontx.gov` (CKAN).
7. **Ledger dedup is logical.** Event dedup keys on
   (property, event_type, event_date, upstream `source_name` + `source_record_id`)
   across content versions — a refreshed payload must never duplicate a public event.
8. **Verification levels are a closed set** (`app/enums.py::VerificationLevel`,
   seven values). Never invent labels like a bare "verified".

## Layout

```
apps/api   FastAPI + SQLAlchemy 2 + Alembic (Python 3.12 via uv)
apps/web   Next.js App Router + Tailwind (TS strict)
packages/shared  API contract types + JSON fixtures (parity-tested on both sides)
data/fixtures    synthetic, committed; data/raw + data/staging are gitignored scratch
plan/      implementation plan + frozen contracts
docs/      per spec §67
```

## Environment & commands

Postgres/Redis/MinIO run in Docker; the db is published on **host port 5433**
(5432 is often taken locally) — inside compose it's `db:5432`.

```bash
cp .env.example .env
make dev        # full stack up (web :3000, api :8000)
make migrate    # alembic upgrade (runs in the api container when the stack is up)
make seed       # synthetic demo: 100/200/300 Test Street
make test       # backend pytest + web vitest
make lint       # ruff + mypy + eslint
```

Backend, directly:
```bash
cd apps/api
uv run pytest -q                 # needs Postgres on localhost:5433 (docker compose up -d db)
uv run ruff check . && uv run mypy app
```
Tests create their own database (default `knomes_test`; override with
`TEST_DATABASE_URL=postgresql+psycopg://knomes:knomes@localhost:5433/<name>` —
parallel agents must each use their own database name). Never point tests at the
dev database `knomes`.

Web:
```bash
cd apps/web
npm run typecheck && npm test -- --run && npm run build
npx playwright test              # e2e smoke (builds via next start)
```

Imports (idempotent; rerunning must create zero new rows):
```bash
make import-hcad                 # fixture; real sample: scripts/fetch_hcad_sample.py
make import-houston-code         # CKAN fixture mode
```

## Conventions

- Python: typed SQLAlchemy 2 (`Mapped`), enums stored as TEXT validated by
  `StrEnum`, structured one-line JSON logs, no bare `print`. Schema changes go
  through Alembic migrations — never `create_all`.
- New data sources implement `SourceAdapter` (`app/ingestion/base.py`) and run
  through `run_sync` in `app/ingestion/runner.py` — do not reimplement matching,
  dedup, or sync metrics. Unmatched records go to the queue
  (`record_property_matches.property_id IS NULL`); never silently attach an
  uncertain match.
- Frontend types in `apps/web/src/lib/types.ts` mirror `apps/api/app/schemas/`
  1:1 and are enforced by the fixtures in `packages/shared/fixtures/` — if you
  change a response schema, regenerate fixtures
  (`cd apps/api && uv run python scripts/generate_contract_fixtures.py`) and
  update both sides in the same change.
- UI copy: provenance labels come from the fixed seven-value map; submission CTAs
  exist but are disabled ("Coming soon"); the words "Write a review" must never
  appear.
- Don't commit: secrets, `.env`, anything under `data/raw`/`data/staging`,
  real personal data in fixtures.

## Scope guard

Phases 0–3 only unless the plan says otherwise. Explicitly deferred (spec §60/§72):
MLS/Repliers integration, document upload + extraction, moderation UI, auth,
AVM/scores, mobile apps, payments, nationwide coverage. Don't start these
without the owner asking.
