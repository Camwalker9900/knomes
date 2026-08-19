# Knomes — Property Condition Ledger MVP (Phases 0–3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Knomes ("Know your home") verified property-condition and failed-transaction ledger MVP for Houston / Harris County — Phases 0–3 of the spec — plus vectorize the existing gnome app icon without changing its design.

**Architecture:** A monorepo with a FastAPI + SQLAlchemy 2 + PostgreSQL/PostGIS event-ledger backend and a Next.js/Tailwind frontend. All data flows RAW → STAGING → CANONICAL → LEDGER through pluggable `SourceAdapter`s (HCAD, Houston code enforcement), every public event traces to a `source_record`/`evidence` row, and verification levels are explicit — the system never manufactures causality.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 16 + PostGIS + pg_trgm + unaccent, Redis + RQ, MinIO (dev object storage), Next.js (App Router) + TypeScript strict + Tailwind, pytest, Vitest, Playwright, Docker Compose, GitHub Actions.

## Global Constraints (from spec — verbatim invariants)

- Houston / Harris County only. No nationwide ingestion, AVM, mortgages, home search, marketplaces, scores, chatbots, payments, or social features (spec §60).
- **Never scrape** HAR, Zillow, Realtor.com, Redfin, or any proprietary real-estate site. MLS comes later via Repliers behind a `ListingProvider` interface with `MockListingProvider` first (§2, §34). Phases 0–3 do **not** implement MLS.
- Event ledger, not a flattened property table (§4). Never use street address as permanent ID; identity hierarchy: HCAD account ID → parcel geometry → normalized address (§5).
- Verification levels exactly: `GOVERNMENT_RECORD, LICENSED_PROFESSIONAL, TRANSACTION_DOCUMENT, DOCUMENT_SUPPORTED, PARTICIPANT_REPORTED, SYSTEM_INFERRED, UNVERIFIED` (§9). Never a bare "verified".
- `Pending → Active` may yield `outcome=TERMINATED, termination_reason=UNKNOWN, verification_level=SYSTEM_INFERRED` — never `PROPERTY_CONDITION` without evidence (§14, §33, §50).
- Findings statuses: `OPEN, RESOLUTION_REPORTED, RESOLUTION_EVIDENCE_FOUND, RESOLVED, DISPUTED, SUPERSEDED, UNKNOWN` — never `FIXED` (§11). Resolution: computer proposes, human approves (§13).
- Raw source records retained as received; unique on `(source_name, source_record_id, content_hash)` (§7). Every government-record event must have a source record (§50). Provenance chain never lost (§41).
- Uploaded documents private by default; derived findings public only after review (§16). No secrets committed (§54). No hard deletes of history — `retracted_at`/`retraction_reason` (§21).
- No automated legal or engineering conclusions (§47–48). Ten data-integrity rules of §63 are invariants.
- Postgres extensions: `postgis`, `pg_trgm`, `unaccent` (§3). Documents never live in PostgreSQL (§3). No Kafka, no Kubernetes (§3, §70).
- Backend tooling: ruff, mypy, pytest. Frontend: eslint, prettier, TS strict, vitest, playwright (§68). Type hints, small modules, DI for source adapters, no hard-coded paths, migration-backed schema changes, structured JSON logging (§51, §68).
- Do not reorder the §66 task order to prioritize UI polish. Phases 0–3 only (§72): no MLS, no AI extraction, no paid APIs, no production deployment.
- **Logo:** do not change the gnome design — vectorize it faithfully (user instruction).

---

## Repository Layout (target)

```
knomes/                          # repo root = /Users/cameronwalker/Projects/knomes
├── apps/
│   ├── api/                     # FastAPI backend
│   │   ├── app/
│   │   │   ├── main.py          # app factory, /health
│   │   │   ├── core/            # config.py, db.py, logging.py
│   │   │   ├── models/          # SQLAlchemy models, one module per aggregate
│   │   │   ├── enums.py         # all StrEnums (verification, statuses, categories…)
│   │   │   ├── lib/             # address.py (normalizer), hashing.py
│   │   │   ├── api/v1/          # routers: properties.py, health.py
│   │   │   ├── schemas/         # Pydantic response/request models
│   │   │   ├── services/        # search.py, timeline.py, matching.py, resolution.py
│   │   │   ├── ingestion/
│   │   │   │   ├── base.py      # SourceAdapter ABC, RawSnapshot, NormalizedRecord
│   │   │   │   ├── hcad/        # download.py, parse.py, normalize.py, load.py, sync.py
│   │   │   │   └── houston_code/  # adapter.py, normalize.py, sync.py
│   │   │   └── seed/            # synthetic demo dataset
│   │   ├── alembic/             # migrations
│   │   ├── tests/
│   │   └── pyproject.toml       # uv-managed, ruff+mypy config
│   └── web/                     # Next.js frontend
│       ├── src/app/             # page.tsx (search), property/[id]/page.tsx
│       ├── src/components/      # SearchBox, Timeline, EventCard, FindingCard,
│       │                        # ProvenanceBadge, ConditionSummary, Freshness
│       ├── src/lib/api.ts       # typed API client
│       └── src/lib/types.ts     # mirrors packages/shared contracts
├── packages/
│   └── shared/                  # shared API contract types (TS) + JSON fixtures
├── data/
│   ├── raw/        (gitignored)
│   ├── staging/    (gitignored)
│   └── fixtures/                # hcad_sample/, houston_code_sample/
├── infrastructure/              # (placeholder README; no k8s)
├── scripts/                     # fetch_hcad_sample.py, wait_for_db.py
├── logo/                        # raw.png (source), knomes-icon.svg (deliverable),
│   └── exports/                 # rendered PNG checks
├── plan/                        # this document
├── docs/                        # architecture, data-model, data-sources, verification-model,
│                                # privacy, ingestion, moderation, deployment
├── tests/                       # cross-cutting/e2e notes (backend tests live in apps/api/tests)
├── .github/workflows/ci.yml
├── docker-compose.yml
├── Makefile
├── README.md
├── .env.example
└── .gitignore
```

---

## Data Model (canonical DDL summary)

All PKs `UUID` (server default `gen_random_uuid()` via `pgcrypto`), all timestamps `TIMESTAMPTZ`. Enumerations stored as `TEXT` validated by Python `StrEnum` (documented in docs/data-model.md; avoids PG-enum migration pain). Tables, per spec §5–§21, §28, §30–§31:

1. **properties** — `hcad_account_id TEXT UNIQUE NULL`, address parts, `normalized_address`, `address_hash`, lat/lon, `parcel_geometry GEOMETRY(MULTIPOLYGON,4326) NULL`, `year_built`, `building_sqft`, `lot_sqft`, `property_type`, timestamps. Indexes: unique on `hcad_account_id`, GIN trigram on `normalized_address`, GIST on `parcel_geometry`.
2. **property_addresses** — alias table: `property_id FK`, `raw_address`, `normalized_address`, `source`, `valid_from/valid_to`, `is_current`. Trigram index on `normalized_address`.
3. **source_records** — `source_name`, `source_record_id`, `property_id NULL`, `record_type`, `raw_payload JSONB`, `source_url`, `source_date`, `retrieved_at`, `content_hash`, `parser_version`. Unique `(source_name, source_record_id, content_hash)`.
4. **ledger_events** — `property_id`, `event_type` (§8 list), `event_date`, `title`, `summary`, `source_record_id NULL`, `submission_id NULL`, `verification_level`, `confidence FLOAT NULL`, `visibility` (`PUBLIC|PRIVATE|PENDING_REVIEW`), `retracted_at NULL`, `retraction_reason NULL`, timestamps. Index `(property_id, event_date)`. Dedup key: unique partial index on `(property_id, event_type, event_date, source_record_id)` where `source_record_id IS NOT NULL`.
5. **claims** — §10 fields incl. `review_status` (`PENDING_REVIEW|APPROVED|REJECTED|NEEDS_CLARIFICATION`).
6. **findings** — §11 fields; `category` from the §11 list; `status` from §11 statuses; `created_from_claim_id`, `created_from_event_id`.
7. **finding_resolutions** — §12 fields; `resolution_type` from §12 list.
8. **transaction_cycles** — §14 fields; `outcome` and `termination_reason` enums exactly as §14.
9. **evidence** — §15 fields; `visibility` defaults `PRIVATE`; `storage_key` points at object storage, never bytea.
10. **submissions** — §17 fields; `submitter_role` from §17 list; `status` = `PENDING_REVIEW|APPROVED|REJECTED|NEEDS_CLARIFICATION`.
11. **professionals** — §38 fields; `verification_status` = `UNVERIFIED|PENDING|VERIFIED|REJECTED`.
12. **source_sync_runs** — §28 fields.
13. **record_property_matches** — §30: `source_record_id`, `property_id`, `match_method` (`HCAD_ID|EXACT_ADDRESS|ADDRESS_ALIAS|PARCEL_GEOMETRY|FUZZY_ADDRESS|MANUAL`), `confidence`, `review_status`, `match_reason TEXT`. Unmatched records get `review_status='UNMATCHED'` rows with `property_id NULL` (the unmatched queue).
14. **event_relationships** — §31: `from_event_id`, `to_event_id`, `relationship_type` (`SAME_AS|SUPPORTS|RESOLVES|DISPUTES|SUPERSEDES|RELATED_TO`).
15. **resolution_candidates** — §32 output: `finding_id`, `candidate_event_id`, `match_score`, `status` (`REVIEW_REQUIRED|APPROVED|REJECTED`), `rule` TEXT.
16. **audit_log** — §21: `actor_type`, `actor_id`, `action`, `entity_type`, `entity_id`, `previous_value JSONB`, `new_value JSONB`, `timestamp`.

### Key interfaces (produced once, consumed everywhere)

```python
# app/lib/address.py
def normalize_address(raw: str) -> str: ...      # deterministic USPS-style: upper, strip punct,
                                                 # suffix/directional/unit canonicalization
def address_hash(normalized: str) -> str: ...    # sha256 hex of normalized string

# app/ingestion/base.py
@dataclass class RawSnapshot: source_name: str; storage_path: Path; checksum: str; retrieved_at: datetime; source_url: str | None
@dataclass class NormalizedRecord: record_type: str; source_record_id: str; raw_payload: dict; normalized_address: str | None; hcad_account_id: str | None; event_candidates: list[EventCandidate]
class SourceAdapter(ABC):
    source_name: ClassVar[str]
    parser_version: ClassVar[str]
    async def fetch(self) -> RawSnapshot: ...
    def parse(self, snapshot: RawSnapshot) -> Iterator[dict]: ...
    def normalize(self, record: dict) -> NormalizedRecord: ...
    def reconcile(self, session, record: NormalizedRecord, source_record_id: UUID) -> list[LedgerEvent]: ...

# app/services/matching.py
def match_record_to_property(session, rec: NormalizedRecord) -> MatchResult  # method+confidence per §30 ladder

# app/services/resolution.py
def find_candidate_resolutions(session, finding: Finding) -> list[ResolutionCandidate]  # §32; proposes only
```

API contract (§35 subset for Phases 0–3):

```
GET /health
GET /api/v1/properties/search?q=...        → {results: PropertySearchResult[]} (max 10)
GET /api/v1/properties/{id}                → PropertyDetail (+condition summary counts, freshness)
GET /api/v1/properties/{id}/timeline?filter= → {events: TimelineEvent[]}   (public events only)
GET /api/v1/properties/{id}/findings       → {findings: FindingDetail[]}
GET /api/v1/properties/{id}/transactions   → {transactions: TransactionCycleOut[]}
GET /api/v1/properties/{id}/sources        → {sources: SourceProvenance[]} (freshness per source)
```

---

## Workstream A — Logo Vectorization (independent; do first, it's the user's lead request)

### Task A1: Faithful SVG of the gnome icon

**Files:** Create `logo/knomes-icon.svg` (1254×1254 viewBox, full icon with navy rounded-square bg), `logo/knomes-gnome.svg` (gnome only, transparent bg, for favicon/header), `logo/exports/` render checks.

Measured geometry (from raw.png, 1254×1254): bg rounded square full-bleed, corner radius ≈280 (iOS 22.4%); navy `#032042`. Hat orange `#ED5621`, tip at (666,111) bending right; dark-orange brim/shadow `#C04119`. White chevron roof band apex ≈(627,458), eave tips ≈(326,757)/(929,757), band ≈28px thick, rounded tips; dark shadow strip under band. Chimney white, cap ≈(755–855, 470–510), stack ≈(768–845) descending behind band. Window ≈115px white rounded square centered (627,620), orange cross mullions. Face skin `#FDD0A5`, ears at ≈(365,785)/(890,785); eyes: navy `#0A2A4D` thick closed arcs (∩) centered ≈(500,762)/(755,762); nose oval `#F9A76F` ≈(627,792) rx≈67; gray under-nose crescent `#D8D9DA`. Beard white with side points ≈(420,980)/(840,980), round bottom to (627,1125). Door navy arch x 567–686, top ≈930, knob white r≈13 at (660,1030). Body steel blue `#15537F`, x≈338–915, y≈860–1128, rounded.

- [ ] **A1.1** Hand-author SVG with the layer order: bg → body → hat cone → dark brim lobes → face+ears → roof shadow strip → chimney → white chevron → window → eyes/nose-shadow/nose → beard → door+knob.
- [ ] **A1.2** Render via `qlmanage -t -s 1254` and compose side-by-side with raw.png (PIL); inspect and iterate until layout matches (positions within ~1% and all shapes present; smooth curves, no lumps).
- [ ] **A1.3** Derive transparent-bg gnome variant; add both to git; wire `knomes-gnome.svg` as web app favicon + header mark in Workstream D.
- [ ] **A1.4** Commit `feat(logo): vectorize gnome icon`.

**Explicitly not:** redesigning any element; auto-tracing (produces lumpy paths); changing colors.

---## Workstream B — Phase 0: Foundation

### Task B1: Repo + tooling scaffold

**Files:** `.gitignore`, `README.md`, `Makefile`, `.env.example`, `docker-compose.yml`, dir skeleton above.

- [ ] **B1.1** `git init`, root `.gitignore` (python, node, `.env`, `data/raw`, `data/staging`, `*.pyc`, `.venv`, `node_modules`, `.next`).
- [ ] **B1.2** `docker-compose.yml` services: `db` (`postgis/postgis:16-3.4`, healthcheck `pg_isready`), `redis` (`redis:7`), `minio` (`minio/minio`, console 9001), `api` (build `apps/api`, uvicorn --reload, depends_on db healthy), `worker` (same image, `rq worker`), `web` (build `apps/web`, `npm run dev`). Volumes for pg + minio.
- [ ] **B1.3** Makefile targets exactly per §53: `dev stop test lint format migrate seed import-hcad import-houston-code reset-db` (+ `test-api test-web`). `make dev` = `docker compose up -d --build`.
- [ ] **B1.4** `.env.example` exactly per §54 (with dev-default values commented).
- [ ] **B1.5** Commit `chore: monorepo scaffold, docker dev environment`.

### Task B2: FastAPI skeleton + structured logging + /health

**Files:** `apps/api/pyproject.toml` (deps: fastapi, uvicorn, pydantic-settings, sqlalchemy>=2, alembic, psycopg[binary], geoalchemy2, redis, rq, httpx, python-json-logger; dev: pytest, ruff, mypy, types-*), `app/main.py`, `app/core/{config,db,logging}.py`, `app/api/v1/health.py`, `apps/api/Dockerfile`, `tests/test_health.py`.

- [ ] **B2.1** Failing test: `GET /health` returns `{"status":"ok","database":..,"redis":..,"storage":..}` (§51); components report `"ok"`/`"unavailable"`, endpoint 200 either way with overall status reflecting DB.
- [ ] **B2.2** Implement settings (DATABASE_URL, REDIS_URL, S3_*, APP_ENV, APP_SECRET), JSON logging config, health checks with short timeouts.
- [ ] **B2.3** `ruff check`, `mypy`, `pytest` pass → commit `feat(api): fastapi skeleton, health endpoint, json logging`.

### Task B3: Next.js skeleton

**Files:** `apps/web/` — package.json (next, react, tailwind, typescript strict; dev: vitest, @testing-library/react, playwright, eslint, prettier), `src/app/layout.tsx`, `src/app/page.tsx` placeholder, `apps/web/Dockerfile`.

- [ ] **B3.1** Scaffold manually (pinned deps), TS `strict: true`, Tailwind configured, `npm run build` passes.
- [ ] **B3.2** Vitest smoke test renders home page headline `Know what happened to a house.` (§26/§57).
- [ ] **B3.3** Commit `feat(web): nextjs skeleton with tailwind + vitest`.

### Task B4: CI

**Files:** `.github/workflows/ci.yml`.

- [ ] **B4.1** Jobs per §69: backend lint (ruff), type check (mypy), unit tests (pytest w/ postgis service container), migration smoke test (`alembic upgrade head` against service container), frontend lint/typecheck/unit tests, Playwright smoke (build + start web with mocked API fixture, check search page renders).
- [ ] **B4.2** Commit `ci: github actions pipeline`.

**Phase 0 acceptance:** `make dev` boots all six services; `make test` green.

---

## Workstream C — Phase 1: Core Ledger

### Task C1: Enums + address normalizer (TDD)

**Files:** `app/enums.py`, `app/lib/address.py`, `tests/test_address.py`.

- [ ] **C1.1** Failing tests (from §50): `9219 Timberside Dr.`, `9219 TIMBERSIDE DRIVE`, `9219 Timberside Dr` all normalize to `9219 TIMBERSIDE DR`; unit handling (`#4`, `APT 4`, `UNIT 4` → `APT 4` canonical); directionals (`North`→`N`); suffixes (ST/AVE/BLVD/LN/CT/RD/PKWY/HWY/WAY/CIR/PL/TRL); whitespace/punctuation/case; `unaccent`-style ASCII folding; `address_hash` stability.
- [ ] **C1.2** Implement pure-python deterministic normalizer (token pipeline: uppercase → strip punct → fold accents → map directional + suffix + unit-designator tables → collapse spaces). No network geocoding.
- [ ] **C1.3** Commit `feat(api): deterministic address normalizer`.

### Task C2: Models + initial migration

**Files:** `app/models/*.py` (all 16 tables), `alembic/`, `tests/test_migrations.py`, `tests/conftest.py` (session-scoped PG test database using DATABASE_URL, per-test transaction rollback).

- [ ] **C2.1** Migration 0001: `CREATE EXTENSION IF NOT EXISTS postgis/pg_trgm/unaccent/pgcrypto`; all tables + indexes from the DDL summary above.
- [ ] **C2.2** Test: `alembic upgrade head` then `alembic downgrade base` then `upgrade head` succeeds; all expected tables/extensions exist.
- [ ] **C2.3** Commit `feat(api): core ledger schema + alembic migration 0001`.

### Task C3: Seed synthetic demo (§49, §55, §65)

**Files:** `app/seed/synthetic.py`, `app/seed/__main__.py`, `tests/test_seed.py`.

Three synthetic properties (all `city='Houston'`, `state='TX'`, `postal_code='77000'`, synthetic `hcad_account_id` like `TEST000100`):
- **100 Test Street** — §65 full arc: LISTING_CREATED (Jan 1) → LISTING_UNDER_CONTRACT (Jan 5) → INSPECTION_PERFORMED (Jan 8) → CONDITION_FINDING + REPAIR_RECOMMENDED (HVAC, LICENSED_PROFESSIONAL, Jan 9) → TRANSACTION_TERMINATED (Jan 12, TRANSACTION_DOCUMENT, reason PROPERTY_CONDITION) → PERMIT_ISSUED (Feb 1, GOVERNMENT_RECORD) → REPAIR_REPORTED (Feb 5) → PERMIT_FINALIZED (Feb 8) → FINDING_RESOLVED. Finding status history OPEN → RESOLUTION_EVIDENCE_FOUND → RESOLVED; transaction_cycle outcome=TERMINATED reason=PROPERTY_CONDITION verification=TRANSACTION_DOCUMENT; finding_resolution rows link permit events; event_relationships SUPPORTS/RESOLVES chain; every gov event backed by a synthetic `source_record` (`source_name='synthetic_fixture'`).
- **200 Test Street** — pending→active inferred failure: LISTING_UNDER_CONTRACT → LISTING_BACK_ON_MARKET → TRANSACTION_TERMINATED (SYSTEM_INFERRED, confidence 0.75, reason UNKNOWN, summary "A previous contract appears not to have closed. Reason not verified.").
- **300 Test Street** — foundation dispute: CONDITION_FINDING (STRUCTURAL/foundation) → FINDING_DISPUTED → claim from engineer "No repair recommended."; finding status DISPUTED; both records retained.

- [ ] **C3.1** Failing test: run seed twice → idempotent (row counts equal); 100 Test St has RESOLVED finding w/ resolution linked to PERMIT_FINALIZED event; 200 Test St termination reason is UNKNOWN; 300 Test St finding DISPUTED with both sides present.
- [ ] **C3.2** Implement with natural keys (upsert by hcad_account_id / (property, event_type, event_date, title)).
- [ ] **C3.3** `make seed` wired. Commit `feat(api): synthetic demo fixtures`.

### Task C4: Search + property + timeline services & endpoints

**Files:** `app/services/{search,timeline}.py`, `app/api/v1/properties.py`, `app/schemas/*.py`, `tests/test_search.py`, `tests/test_timeline_api.py`.

- [ ] **C4.1** Failing tests: search `100 test street` → exact hit first; `100 test st.` → same; `TEST000200` → HCAD-id hit; misspelled `100 Tset Street` → trigram hit; max 10 results. Timeline for 100 Test St returns events chronological with `verification_level`, `provenance` (source name + record type) on each; `?filter=permits` subsets; PRIVATE/PENDING events excluded; property detail includes condition summary counts (§22) and per-source freshness (§42).
- [ ] **C4.2** Implement search priority ladder (§26): exact normalized → hcad account → `similarity()` w/ pg_trgm ≥0.3 ordered desc. Timeline filter groups: `all transactions inspections findings repairs permits code ownership` (§23).
- [ ] **C4.3** Commit `feat(api): property search, detail, timeline endpoints`.

**Phase 1 acceptance:** searching `100 Test Street` via API returns the coherent §65 timeline.

---

## Workstream D — Frontend (Phase 1 UI; §22–§26, §57–§58)

### Task D1: Typed client + shared contracts
**Files:** `packages/shared/src/contracts.ts` (+ JSON schema fixtures exported from pytest for contract parity), `apps/web/src/lib/{api,types}.ts`.
- [ ] D1.1 Types mirror Pydantic schemas 1:1 (PropertySearchResult, PropertyDetail, TimelineEvent{event_type, event_date, title, summary, verification_level, confidence, provenance}, FindingDetail, TransactionCycleOut, SourceProvenance). Contract test: backend serializes fixture → JSON checked into `packages/shared/fixtures/` → vitest asserts client types parse it.
- [ ] D1.2 Commit `feat(web): typed api client + shared contracts`.

### Task D2: Home/search page (§57)
- [ ] D2.1 Headline `Know what happened to a house before you buy it.`, search box with debounced autocomplete against `/search`, keyboard accessible (combobox pattern), section listing `Permits / Inspections / Repairs / Code violations / Previous transactions / Verified property findings`. Knomes gnome mark + wordmark in header. Vitest: renders + shows results on mock fetch.
- [ ] D2.2 Commit.

### Task D3: Property page (§22–§25)
- [ ] D3.1 `/property/[id]`: header (address, built year, sqft, lot, HCAD account), Condition Summary counts, central Timeline (year-grouped dots, filters per §23), FindingCard with status/severity/resolution chain (§24), ProvenanceBadge component with click-to-explain popover for the six §25 labels, transactions section (reason shown only at its verification level; UNKNOWN wording per §33), sources/freshness footer (§42), submission CTA block per §18 (buttons present, disabled with "coming soon" — flows are Phase 5; **never** "Write a review").
- [ ] D3.2 Vitest component tests: badge label mapping; timeline renders §65 fixture; SYSTEM_INFERRED termination renders "A previous contract appears not to have closed. Reason not verified."
- [ ] D3.3 Playwright smoke: seed → search `100 Test Street` → property page shows finding OPEN→RESOLVED arc. Commit.

---

## Workstream E — Phases 2–3: Ingestion

### Task E1: Adapter framework + sync runner + matching (§28–§30, §43–§44)

**Files:** `app/ingestion/base.py`, `app/ingestion/runner.py` (generic: fetch → snapshot to `data/raw/` + object storage → parse → staging insert → normalize → match → upsert source_records → reconcile to ledger_events → source_sync_runs metrics), `app/services/matching.py`, `tests/test_matching.py`, `tests/test_runner_idempotency.py`.

- [ ] E1.1 Failing tests: match ladder — record w/ HCAD id → confidence 1.0 method HCAD_ID; exact normalized address → 0.99; alias → ADDRESS_ALIAS; fuzzy above threshold → FUZZY_ADDRESS w/ review_status REVIEW_REQUIRED; below threshold → unmatched queue row, **no** property attach (§29 "never silently attach"). Running the same import twice creates zero new source_records/ledger_events (content_hash dedup).
- [ ] E1.2 Implement; every match stores `match_reason`. Commit.

### Task E2: HCAD adapter (§27, Phase 2)

**Files:** `app/ingestion/hcad/{download,parse,normalize,load,sync}.py`, fixture `data/fixtures/hcad_sample/real_acct.txt` (tab-delimited, HCAD column layout, ~25 rows synthetic-but-format-real), `scripts/fetch_hcad_sample.py` (downloads real 2026 Real_acct_owner.zip from download.hcad.org, extracts first N rows — run manually; URL in config), `tests/test_hcad_*.py`.

- [ ] E2.1 Failing tests: parse streams rows without loading file to RAM (iterator over opened file); normalize maps acct→hcad_account_id, situs fields→address parts + normalized_address, yr_impr→year_built, im_sq_ft→building_sqft, land_ar→lot_sqft; load upserts properties keyed by hcad_account_id (re-import → no duplicates, §50); raw snapshot checksummed + source_sync_runs row with counts (§28); rejected rows counted not crashed.
- [ ] E2.2 Implement with `COPY` into `hcad_staging` then set-based upsert (§27 pipeline). `python -m app.ingestion.hcad.sync [--file PATH]`; `make import-hcad`.
- [ ] E2.3 Commit. Real-sample demo happens in Verification (V3).

### Task E3: Houston code-enforcement adapter (§29, Phase 3)

**Files:** `app/ingestion/houston_code/{adapter,normalize,sync}.py` (CKAN `datastore_search` pagination via httpx; dataset/resource id in settings), fixture `data/fixtures/houston_code_sample/records.json`, `tests/test_houston_code.py`.

- [ ] E3.1 Failing tests: normalize maps project/violation/action/status/dates; reconcile emits CODE_VIOLATION_OPENED/ACTION/RESOLVED events (GOVERNMENT_RECORD, confidence 1.0) each with source_record provenance; address resolution uses E1 ladder; unmatched → queue; idempotent re-run.
- [ ] E3.2 Implement; `make import-houston-code`. Commit.

### Task E4: Resolution matching engine (§32, propose-only)

**Files:** `app/services/resolution.py`, `tests/test_resolution.py`.

- [ ] E4.1 Failing tests: HVAC finding + later MECHANICAL/HVAC permit on same property → resolution_candidate REVIEW_REQUIRED score≥0.8; **HVAC permit does not close a ROOF finding** (§50); candidate never mutates finding.status; audit_log row written on candidate creation.
- [ ] E4.2 Category→permit-type rule table (HVAC↔MECHANICAL, ELECTRICAL↔ELECTRICAL, PLUMBING/SEWER↔PLUMBING, ROOF/STRUCTURAL/FOUNDATION↔BUILDING). Commit.

---

## Workstream F — Docs (§67) + polish

- [ ] F1 `README.md` (quickstart exactly: clone → cp .env.example .env → make dev/migrate/seed → open localhost:3000 → search `100 Test Street`), `docs/architecture.md`, `data-model.md`, `data-sources.md` (per-source table: owner, acquisition, license, refresh, fields, limitations, adapter, last-verified — HCAD, Houston Open Data CKAN, Houston permits [status: acquisition method under investigation, adapter interface ready], Harris County Clerk [Phase 2 investigation, interface only], MLS/Repliers [deferred]), `verification-model.md` (§9, §25, §33 wording rules), `privacy.md` (§16, §37), `ingestion.md` (§44 pipeline + §28 reproducibility), `moderation.md` (§20 design, Phase 5), `deployment.md` (§70 shape, "not yet deployed").
- [ ] F2 Commit.

## Workstream V — Verification (Definition of Done for this engagement)

- [ ] V1 Start Docker daemon; `make dev` → all services healthy; `make migrate seed`.
- [ ] V2 `make test` (api + web) green; ruff/mypy/eslint/tsc green; Playwright smoke green.
- [ ] V3 HCAD sample import: run `scripts/fetch_hcad_sample.py` if download.hcad.org reachable (else fixture file), `make import-hcad`, verify a real Harris County address resolves via search and shows provenance + freshness. Houston code import against fixture (live CKAN attempt if reachable) and verify chronological violation timeline on a matched property.
- [ ] V4 Browser check: localhost:3000 → search `100 Test Street` → §65 arc renders with badges; screenshot.
- [ ] V5 Final review pass (adversarial, multi-agent): §63 invariants, §50 test list coverage, provenance chain, no secrets, no scraping code. Fix findings; re-run tests; final commit.

**Out of scope (per §72):** MLS/Repliers integration, AI/LLM extraction, auth flows, upload pipeline, moderation UI, paid APIs, production deployment, Harris County Clerk automation.

## Execution notes

- Executor: this session, ultracode enabled — scaffold + schema built inline for coherence; independent modules (frontend components, docs, adapters, test suites) fanned out via Workflow subagents against the interfaces frozen in this plan; adversarial review workflow at V5.
- Local tooling: python3.12 via `uv`; Node 24/npm 11; Docker Desktop (daemon must be started for V1–V4).
- Commit after each task with conventional messages; repo root is `/Users/cameronwalker/Projects/knomes`.
