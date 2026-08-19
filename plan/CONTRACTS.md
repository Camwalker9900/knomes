# Knomes — Frozen Interface Contracts (for parallel build agents)

Repo root: `/Users/cameronwalker/Projects/knomes`. Every agent writes ONLY the files in its own task list. Anything named here is a contract: use these exact names/shapes. Read `plan/2026-08-19-knomes-mvp-phases-0-3.md` for full context.

## Conventions
- Python 3.12, managed by `uv` (`apps/api/pyproject.toml`, `[project]` + `[dependency-groups] dev`). Run: `cd apps/api && uv run pytest -q`, `uv run ruff check .`, `uv run mypy app`.
- SQLAlchemy 2.0 typed ORM (`Mapped`/`mapped_column`), sync engine, driver `postgresql+psycopg://`.
- All PKs: `id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)`.
- Timestamps: `TIMESTAMPTZ` (`DateTime(timezone=True)`); `created_at` default now, `updated_at` onupdate now.
- Enums stored as TEXT in PG; Python `enum.StrEnum` in `app/enums.py`; model columns typed `Mapped[str]` with values from those enums.
- Ports: db host 5433 -> container 5432 (in-cluster: db:5432), redis 6379, minio 9000 (console 9001), api 8000, web 3000.
- Dev DATABASE_URL: `postgresql+psycopg://knomes:knomes@localhost:5433/knomes`. Tests use `TEST_DATABASE_URL` env or `postgresql+psycopg://knomes:knomes@localhost:5433/knomes_test`.
- Structured JSON logging to stdout. No secrets committed. Type hints everywhere.

## app/core/config.py
```python
class Settings(BaseSettings):  # pydantic-settings, env file ".env", extra="ignore"
    database_url: str = "postgresql+psycopg://knomes:knomes@localhost:5433/knomes"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "knomes-dev"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    repliers_api_key: str = ""
    admin_email: str = "admin@example.com"
    app_env: str = "development"
    app_secret: str = "dev-secret-change-me"
    hcad_download_url: str = ""     # set when licensing/URL confirmed
    houston_ckan_base_url: str = "https://data.houstontx.gov"
    houston_code_resource_id: str = ""
settings = Settings()
```

## app/core/db.py
```python
class Base(DeclarativeBase): pass
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
def get_session() -> Iterator[Session]:  # FastAPI dependency, yields and closes
```

## app/enums.py (exact members)
- `VerificationLevel`: GOVERNMENT_RECORD, LICENSED_PROFESSIONAL, TRANSACTION_DOCUMENT, DOCUMENT_SUPPORTED, PARTICIPANT_REPORTED, SYSTEM_INFERRED, UNVERIFIED
- `EventType`: PROPERTY_CREATED, OWNERSHIP_TRANSFER, DEED_RECORDED, LIEN_RECORDED, LIEN_RELEASED, PERMIT_APPLIED, PERMIT_ISSUED, PERMIT_INSPECTION, PERMIT_FINALIZED, CODE_VIOLATION_OPENED, CODE_VIOLATION_ACTION, CODE_VIOLATION_RESOLVED, LISTING_CREATED, LISTING_PRICE_CHANGED, LISTING_ACTIVE, LISTING_UNDER_CONTRACT, LISTING_PENDING, LISTING_BACK_ON_MARKET, LISTING_CLOSED, LISTING_WITHDRAWN, INSPECTION_PERFORMED, CONDITION_FINDING, REPAIR_RECOMMENDED, TRANSACTION_TERMINATED, REPAIR_REPORTED, REPAIR_VERIFIED, FINDING_DISPUTED, FINDING_RESOLVED, FINDING_REOPENED
- `FindingCategory`: ROOF, HVAC, ELECTRICAL, PLUMBING, SEWER, FOUNDATION, STRUCTURAL, WATER_INTRUSION, DRAINAGE, MOLD, PEST, POOL, APPLIANCE, WINDOWS, EXTERIOR, INTERIOR, SAFETY, OTHER
- `FindingStatus`: OPEN, RESOLUTION_REPORTED, RESOLUTION_EVIDENCE_FOUND, RESOLVED, DISPUTED, SUPERSEDED, UNKNOWN
- `FindingSeverity`: MINOR, MODERATE, MAJOR, SAFETY
- `ResolutionType`: REPAIR, REPLACEMENT, PROFESSIONAL_REEVALUATION, PERMIT_FINALIZATION, SELLER_DOCUMENTATION, INSPECTION_CLEARANCE, OTHER
- `TransactionOutcome`: UNKNOWN, CLOSED, TERMINATED, WITHDRAWN, EXPIRED
- `TerminationReason`: UNKNOWN, PROPERTY_CONDITION, FINANCING, APPRAISAL, TITLE, INSURANCE, BUYER_DECISION, SELLER_DECISION, CONTINGENCY, OTHER
- `EvidenceType`: INSPECTION_REPORT, HVAC_REPORT, ROOF_REPORT, PLUMBING_REPORT, ELECTRICAL_REPORT, STRUCTURAL_REPORT, SEWER_SCOPE, SELLER_DISCLOSURE, TERMINATION_NOTICE, REPAIR_AMENDMENT, CONTRACTOR_QUOTE, INVOICE, RECEIPT, PERMIT_DOCUMENT, PHOTO, OTHER
- `SubmitterRole`: BUYER, SELLER, INSPECTOR, HVAC_CONTRACTOR, ELECTRICIAN, PLUMBER, ROOFER, ENGINEER, REAL_ESTATE_AGENT, OTHER
- `ReviewStatus`: PENDING_REVIEW, APPROVED, REJECTED, NEEDS_CLARIFICATION
- `Visibility`: PUBLIC, PRIVATE, PENDING_REVIEW
- `MatchMethod`: HCAD_ID, EXACT_ADDRESS, ADDRESS_ALIAS, PARCEL_GEOMETRY, FUZZY_ADDRESS, MANUAL
- `MatchReviewStatus`: AUTO_ACCEPTED, REVIEW_REQUIRED, APPROVED, REJECTED, UNMATCHED
- `RelationshipType`: SAME_AS, SUPPORTS, RESOLVES, DISPUTES, SUPERSEDES, RELATED_TO
- `CandidateStatus`: REVIEW_REQUIRED, APPROVED, REJECTED
- `ProfessionalVerificationStatus`: UNVERIFIED, PENDING, VERIFIED, REJECTED
- `ClaimantType`: BUYER, SELLER, INSPECTOR, CONTRACTOR, ENGINEER, AGENT, OTHER

## app/lib/address.py
```python
def normalize_address(raw: str) -> str
    # deterministic: uppercase → ASCII-fold → strip punctuation → tokenize →
    # canonicalize directionals (NORTH→N …), suffixes (DRIVE/DR.→DR, STREET→ST, AVENUE→AVE,
    # BOULEVARD→BLVD, LANE→LN, COURT→CT, ROAD→RD, PARKWAY→PKWY, HIGHWAY→HWY, CIRCLE→CIR,
    # PLACE→PL, TRAIL→TRL, WAY→WAY, TERRACE→TER, SQUARE→SQ, LOOP→LOOP, COVE→CV, BEND→BND),
    # unit designators (#4 / UNIT 4 / APT 4 / STE 4 → "APT 4", except STE stays "STE") → single spaces
def address_hash(normalized: str) -> str  # sha256 hexdigest
```
Required behavior: `"9219 Timberside Dr."`, `"9219 TIMBERSIDE DRIVE"`, `"9219 Timberside Dr"` → `"9219 TIMBERSIDE DR"`.

## Models (app/models/, all inherit Base; class → table)
Property→properties, PropertyAddress→property_addresses, SourceRecord→source_records, LedgerEvent→ledger_events, Claim→claims, Finding→findings, FindingResolution→finding_resolutions, TransactionCycle→transaction_cycles, Evidence→evidence, Submission→submissions, Professional→professionals, SourceSyncRun→source_sync_runs, RecordPropertyMatch→record_property_matches, EventRelationship→event_relationships, ResolutionCandidate→resolution_candidates, AuditLog→audit_log.
Columns exactly as the plan's "Data Model" section (§5–§21, §28, §30–§32 of the spec). Notable:
- properties: hcad_account_id TEXT UNIQUE NULL; normalized_address; address_hash; parcel_geometry `geoalchemy2.Geometry("MULTIPOLYGON", srid=4326)` nullable; GIN trigram index on normalized_address (`postgresql_using="gin"`, `postgresql_ops={"normalized_address": "gin_trgm_ops"}`).
- ledger_events: property_id FK, event_type, event_date TIMESTAMPTZ, title, summary TEXT NULL, source_record_id FK NULL, submission_id FK NULL, verification_level, confidence FLOAT NULL, visibility default PUBLIC, retracted_at NULL, retraction_reason NULL. Index (property_id, event_date).
- source_records: UniqueConstraint(source_name, source_record_id, content_hash).
- transaction_cycles: listing_id TEXT NULL, started_at, under_contract_at NULL, terminated_at NULL, closed_at NULL, outcome, termination_reason, reason_verification_level, evidence_id FK NULL.
- record_property_matches: source_record_id FK, property_id FK NULL, match_method, confidence FLOAT, review_status, match_reason TEXT.
- audit_log: actor_type, actor_id NULL, action, entity_type, entity_id, previous_value JSONB NULL, new_value JSONB NULL, timestamp default now.
`app/models/__init__.py` re-exports all models.

## Alembic
`apps/api/alembic/` + `alembic.ini` (script_location apps/api/alembic; sqlalchemy.url read from settings/env in env.py). Migration 0001 creates extensions `postgis`, `pg_trgm`, `unaccent` + all tables/indexes. Autogenerate must be configured to ignore PostGIS-owned tables (`spatial_ref_sys`).

## API v1 (FastAPI app factory `create_app()` in app/main.py, `app = create_app()`)
- `GET /health` → `{"status":"ok"|"degraded","database":"ok"|"unavailable","redis":"ok"|"unavailable","storage":"ok"|"unavailable"}` (checks best-effort, short timeouts; storage check = HTTP HEAD/GET to settings.s3_endpoint health endpoint, tolerate failure).
- `GET /api/v1/properties/search?q=` → `{"results": [PropertySearchResult]}` max 10. Ladder: exact normalized_address (also property_addresses aliases) → hcad_account_id exact (case-insensitive strip) → pg_trgm `similarity(normalized_address, :q_norm) > 0.3` ordered desc.
- `GET /api/v1/properties/{id}` → PropertyDetail | 404
- `GET /api/v1/properties/{id}/timeline?filter=` → `{"events":[TimelineEvent]}` — only visibility=PUBLIC and retracted_at IS NULL, ordered event_date asc. filter ∈ all|transactions|inspections|findings|repairs|permits|code|ownership (mapping: transactions=LISTING_*+TRANSACTION_TERMINATED; inspections=INSPECTION_PERFORMED; findings=CONDITION_FINDING+FINDING_*; repairs=REPAIR_*; permits=PERMIT_*; code=CODE_VIOLATION_*; ownership=PROPERTY_CREATED+OWNERSHIP_TRANSFER+DEED_RECORDED+LIEN_*).
- `GET /api/v1/properties/{id}/findings` → `{"findings":[FindingDetail]}`
- `GET /api/v1/properties/{id}/transactions` → `{"transactions":[TransactionCycleOut]}`
- `GET /api/v1/properties/{id}/sources` → `{"sources":[SourceProvenance]}`

### Response schemas (Pydantic, app/schemas/) — JSON field names are the contract for the web client
```
PropertySearchResult: id, address_line1, unit|null, city, state, postal_code, hcad_account_id|null, match_type ("EXACT_ADDRESS"|"HCAD_ID"|"FUZZY_ADDRESS")
PropertyDetail: id, address_line1, unit|null, city, state, postal_code, hcad_account_id|null, year_built|null,
  building_sqft|null, lot_sqft|null, property_type|null, latitude|null, longitude|null,
  condition_summary: {open_findings:int, resolved_findings:int, disputed_findings:int, verified_inspections:int, prior_transactions:int},
  freshness: [{source:str, last_refreshed:str|null}]
TimelineEvent: id, event_type, event_date, title, summary|null, verification_level, confidence|null,
  provenance: {source_name, record_type}|null
FindingDetail: id, category, subcategory|null, title, description|null, severity|null, status,
  first_observed_at|null, latest_observed_at|null,
  resolutions: [{id, resolution_type, description|null, verification_level, resolved_at|null, event_id|null}]
TransactionCycleOut: id, started_at|null, under_contract_at|null, terminated_at|null, closed_at|null,
  outcome, termination_reason, reason_verification_level
SourceProvenance: source, record_count:int, last_refreshed:str|null
```
Dates serialize ISO-8601. IDs serialize as string UUIDs.

## Seed (app/seed/)
`python -m app.seed` seeds the three synthetic properties (plan Task C3: 100/200/300 Test Street, Houston TX 77000, hcad ids TEST000100/200/300, source_name "synthetic_fixture"). Idempotent by natural keys. Public wording for 200 Test St inferred termination summary: exactly "A previous contract appears not to have closed. Reason not verified."

## Ingestion (app/ingestion/)
```python
@dataclass class RawSnapshot: source_name: str; storage_path: pathlib.Path; checksum: str; retrieved_at: datetime; source_url: str | None
@dataclass class EventCandidate: event_type: str; event_date: datetime; title: str; summary: str | None; verification_level: str; confidence: float | None
@dataclass class NormalizedRecord: record_type: str; source_record_id: str; raw_payload: dict; normalized_address: str | None; hcad_account_id: str | None; event_candidates: list[EventCandidate]; raw_address: str | None = None
class SourceAdapter(ABC):  # app/ingestion/base.py
    source_name: ClassVar[str]; parser_version: ClassVar[str]
    async def fetch(self) -> RawSnapshot
    def parse(self, snapshot: RawSnapshot) -> Iterator[dict]
    def normalize(self, record: dict) -> NormalizedRecord
```
`app/ingestion/runner.py`: `run_sync(adapter, session_factory, *, snapshot=None) -> SourceSyncRun` — parse→normalize→match (services.matching)→upsert source_records (dedup on unique triple)→create ledger events from event_candidates for matched records only (never attach uncertain matches)→write SourceSyncRun metrics. Idempotent.
`app/services/matching.py`: `match_record_to_property(session, rec: NormalizedRecord) -> MatchResult` where `@dataclass MatchResult: property_id: uuid.UUID | None; method: str | None; confidence: float; reason: str; review_status: str`. Ladder: HCAD_ID 1.0 AUTO_ACCEPTED → EXACT_ADDRESS 0.99 AUTO_ACCEPTED → ADDRESS_ALIAS 0.97 AUTO_ACCEPTED → FUZZY_ADDRESS (trgm similarity ≥0.55) REVIEW_REQUIRED (property_id set but events NOT created) → UNMATCHED (property_id None).
`app/services/resolution.py`: `find_candidate_resolutions(session, finding) -> list[ResolutionCandidate]` — proposes only (CandidateStatus.REVIEW_REQUIRED), never mutates finding.status. Rule table: HVAC→{MECHANICAL,HVAC}, ELECTRICAL→{ELECTRICAL}, PLUMBING/SEWER→{PLUMBING}, ROOF/FOUNDATION/STRUCTURAL→{BUILDING}; permit event after finding.first_observed_at on same property.
HCAD staging table `hcad_staging` loaded via psycopg COPY; upsert properties on hcad_account_id.

## Web (apps/web, Next.js App Router, TS strict, Tailwind)
- `src/lib/types.ts` mirrors the response schemas above.
- `src/lib/api.ts`: `searchProperties(q)`, `getProperty(id)`, `getTimeline(id, filter?)`, `getFindings(id)`, `getTransactions(id)`, `getSources(id)`; base URL from `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).
- Routes: `/` (headline "Know what happened to a house before you buy it.", search box w/ debounced autocomplete), `/property/[id]`.
- Components: SearchBox, ConditionSummary, Timeline (filters: All Transactions Inspections Findings Repairs Permits Code enforcement Ownership), EventCard, FindingCard, ProvenanceBadge (labels: GOVERNMENT_RECORD→"Government Record", LICENSED_PROFESSIONAL→"Licensed Professional", TRANSACTION_DOCUMENT→"Transaction Document", DOCUMENT_SUPPORTED→"Document Supported", PARTICIPANT_REPORTED→"Participant Report", SYSTEM_INFERRED→"System Inference", UNVERIFIED→"Unverified"; click → explanation popover), TransactionsSection, SourcesFooter, SubmitCTA (§18 buttons, disabled "Coming soon"; never "Write a review").
- Favicon/header mark: `/logo/knomes-icon.svg` copied to `apps/web/src/app/icon.svg` + used in header next to wordmark "Knomes".
- Brand styling: navy `#032042`, orange `#ED5621`, steel blue `#15537F` as Tailwind theme colors (navy/flame/steel).

## Makefile targets (root)
dev, stop, test, test-api, test-web, lint, format, migrate, seed, import-hcad, import-houston-code, reset-db — compose project name `knomes`. `migrate`/`seed` run inside the api container if up, else via uv locally.
