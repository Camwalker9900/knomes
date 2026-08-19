# Data Model

Sixteen canonical tables. All primary keys are UUIDs; all timestamps are `TIMESTAMPTZ` (`created_at` default now, `updated_at` on update). Enums are stored as `TEXT` in PostgreSQL and validated by Python `StrEnum`s in `app/enums.py` — this avoids PG-enum migration pain. Column lists below summarize the canonical DDL; the Alembic migrations in `apps/api/alembic/` are authoritative.

Related docs: [architecture.md](architecture.md) · [verification-model.md](verification-model.md) · [ingestion.md](ingestion.md) · [privacy.md](privacy.md)

## Identity hierarchy

A street address is **never** a permanent identifier. Property identity resolves in strict order:

1. **HCAD account ID** (`properties.hcad_account_id`, unique) — the authoritative Harris County parcel key.
2. **Parcel geometry** (`properties.parcel_geometry`, `GEOMETRY(MULTIPOLYGON, 4326)`) — spatial containment when an account ID is absent.
3. **Normalized address** (`properties.normalized_address` + `address_hash`) — deterministic normalization via `app/lib/address.py` (`"9219 Timberside Dr."` → `"9219 TIMBERSIDE DR"`), with historical aliases in `property_addresses`.

## Tables

### properties
| Column | Notes |
|---|---|
| id | UUID PK |
| hcad_account_id | TEXT, UNIQUE, NULL |
| address_line1, unit, city, state, postal_code | address parts |
| normalized_address, address_hash | normalizer output + sha256; GIN trigram index (`gin_trgm_ops`) on normalized_address |
| latitude, longitude | NULL |
| parcel_geometry | MULTIPOLYGON srid 4326, NULL; GIST index |
| year_built, building_sqft, lot_sqft, property_type | from HCAD, NULL |
| created_at, updated_at | |

### property_addresses (alias table)
property_id FK · raw_address · normalized_address (trigram index) · source · valid_from / valid_to · is_current. Captures renames and variants so old addresses still resolve.

### source_records
source_name · source_record_id · property_id FK NULL · record_type · raw_payload JSONB (as received, retained forever) · source_url · source_date · retrieved_at · content_hash · parser_version. **Unique `(source_name, source_record_id, content_hash)`** — the ingestion dedup key.

### ledger_events
property_id FK · event_type · event_date · title · summary NULL · source_record_id FK NULL · submission_id FK NULL · verification_level · confidence FLOAT NULL · visibility (default PUBLIC) · retracted_at NULL · retraction_reason NULL. Index `(property_id, event_date)`; **dedup**: partial unique on `(property_id, event_type, event_date, source_record_id)` where `source_record_id IS NOT NULL`. History is never hard-deleted — retraction sets `retracted_at`/`retraction_reason`.

### claims
property_id FK · submission_id FK NULL · claimant_type (`ClaimantType`) · statement · claim_date · review_status (`ReviewStatus`). A claim is what a participant *asserts*; it becomes public only as a reviewed finding (see [verification-model.md](verification-model.md)).

### findings
property_id FK · category (`FindingCategory`) · subcategory NULL · title · description NULL · severity (`FindingSeverity`) NULL · status (`FindingStatus`) · first_observed_at / latest_observed_at NULL · created_from_claim_id FK NULL · created_from_event_id FK NULL.

### finding_resolutions
finding_id FK · resolution_type (`ResolutionType`) · description NULL · verification_level · resolved_at NULL · event_id FK NULL (e.g. the PERMIT_FINALIZED event that evidences the repair).

### transaction_cycles
property_id FK · listing_id TEXT NULL · started_at · under_contract_at NULL · terminated_at NULL · closed_at NULL · outcome (`TransactionOutcome`) · termination_reason (`TerminationReason`) · reason_verification_level · evidence_id FK NULL.

### evidence
submission_id FK NULL · evidence_type (`EvidenceType`) · **storage_key** (object storage path — never `bytea`; documents never live in Postgres) · content_hash · visibility (default **PRIVATE**).

### submissions
property_id FK · submitter_role (`SubmitterRole`) · submitter contact fields (never exposed publicly; see [privacy.md](privacy.md)) · status (`ReviewStatus`).

### professionals
name · license fields · verification_status (`ProfessionalVerificationStatus`).

### source_sync_runs
source_name · started_at / finished_at · snapshot storage path · snapshot checksum · source_url · parser_version · counters (parsed / inserted / matched / unmatched / rejected). Full reproducibility record per import run.

### record_property_matches
source_record_id FK · property_id FK NULL · match_method (`MatchMethod`) · confidence FLOAT · review_status (`MatchReviewStatus`) · match_reason TEXT. Unmatched records get `review_status='UNMATCHED'`, `property_id NULL` — the unmatched queue.

### event_relationships
from_event_id FK · to_event_id FK · relationship_type (`RelationshipType`). E.g. a PERMIT_FINALIZED event `RESOLVES` a CONDITION_FINDING event.

### resolution_candidates
finding_id FK · candidate_event_id FK · match_score · status (`CandidateStatus`) · rule TEXT. Machine proposals only; approval is human (see [moderation.md](moderation.md)).

### audit_log
actor_type · actor_id NULL · action · entity_type · entity_id · previous_value JSONB NULL · new_value JSONB NULL · timestamp default now.

(`hcad_staging` also exists as a non-canonical COPY target for bulk loads — see [ingestion.md](ingestion.md).)

## Event types (`EventType`)

PROPERTY_CREATED, OWNERSHIP_TRANSFER, DEED_RECORDED, LIEN_RECORDED, LIEN_RELEASED, PERMIT_APPLIED, PERMIT_ISSUED, PERMIT_INSPECTION, PERMIT_FINALIZED, CODE_VIOLATION_OPENED, CODE_VIOLATION_ACTION, CODE_VIOLATION_RESOLVED, LISTING_CREATED, LISTING_PRICE_CHANGED, LISTING_ACTIVE, LISTING_UNDER_CONTRACT, LISTING_PENDING, LISTING_BACK_ON_MARKET, LISTING_CLOSED, LISTING_WITHDRAWN, INSPECTION_PERFORMED, CONDITION_FINDING, REPAIR_RECOMMENDED, TRANSACTION_TERMINATED, REPAIR_REPORTED, REPAIR_VERIFIED, FINDING_DISPUTED, FINDING_RESOLVED, FINDING_REOPENED.

## Enum values (frozen in `app/enums.py`)

| Enum | Members |
|---|---|
| VerificationLevel | GOVERNMENT_RECORD, LICENSED_PROFESSIONAL, TRANSACTION_DOCUMENT, DOCUMENT_SUPPORTED, PARTICIPANT_REPORTED, SYSTEM_INFERRED, UNVERIFIED |
| FindingCategory | ROOF, HVAC, ELECTRICAL, PLUMBING, SEWER, FOUNDATION, STRUCTURAL, WATER_INTRUSION, DRAINAGE, MOLD, PEST, POOL, APPLIANCE, WINDOWS, EXTERIOR, INTERIOR, SAFETY, OTHER |
| FindingStatus | OPEN, RESOLUTION_REPORTED, RESOLUTION_EVIDENCE_FOUND, RESOLVED, DISPUTED, SUPERSEDED, UNKNOWN — never "FIXED" |
| FindingSeverity | MINOR, MODERATE, MAJOR, SAFETY |
| ResolutionType | REPAIR, REPLACEMENT, PROFESSIONAL_REEVALUATION, PERMIT_FINALIZATION, SELLER_DOCUMENTATION, INSPECTION_CLEARANCE, OTHER |
| TransactionOutcome | UNKNOWN, CLOSED, TERMINATED, WITHDRAWN, EXPIRED |
| TerminationReason | UNKNOWN, PROPERTY_CONDITION, FINANCING, APPRAISAL, TITLE, INSURANCE, BUYER_DECISION, SELLER_DECISION, CONTINGENCY, OTHER |
| EvidenceType | INSPECTION_REPORT, HVAC_REPORT, ROOF_REPORT, PLUMBING_REPORT, ELECTRICAL_REPORT, STRUCTURAL_REPORT, SEWER_SCOPE, SELLER_DISCLOSURE, TERMINATION_NOTICE, REPAIR_AMENDMENT, CONTRACTOR_QUOTE, INVOICE, RECEIPT, PERMIT_DOCUMENT, PHOTO, OTHER |
| SubmitterRole | BUYER, SELLER, INSPECTOR, HVAC_CONTRACTOR, ELECTRICIAN, PLUMBER, ROOFER, ENGINEER, REAL_ESTATE_AGENT, OTHER |
| ReviewStatus | PENDING_REVIEW, APPROVED, REJECTED, NEEDS_CLARIFICATION |
| Visibility | PUBLIC, PRIVATE, PENDING_REVIEW |
| MatchMethod | HCAD_ID, EXACT_ADDRESS, ADDRESS_ALIAS, PARCEL_GEOMETRY, FUZZY_ADDRESS, MANUAL |
| MatchReviewStatus | AUTO_ACCEPTED, REVIEW_REQUIRED, APPROVED, REJECTED, UNMATCHED |
| RelationshipType | SAME_AS, SUPPORTS, RESOLVES, DISPUTES, SUPERSEDES, RELATED_TO |
| CandidateStatus | REVIEW_REQUIRED, APPROVED, REJECTED |
| ProfessionalVerificationStatus | UNVERIFIED, PENDING, VERIFIED, REJECTED |
| ClaimantType | BUYER, SELLER, INSPECTOR, CONTRACTOR, ENGINEER, AGENT, OTHER |

## Dedup keys

- **source_records**: unique `(source_name, source_record_id, content_hash)` — a changed upstream payload is a *new* record, an identical re-fetch is a no-op.
- **ledger_events**: partial unique `(property_id, event_type, event_date, source_record_id)` where source_record_id is not null.
- **properties**: unique `hcad_account_id`; seed/import upserts key on it.

## Provenance chain

Every public timeline event resolves to its origin without gaps:

```
ledger_event ─▶ source_record ─▶ source_sync_run (snapshot path, checksum, URL, parser_version)
      └──────▶ submission ─▶ evidence (object-storage document) ─▶ professional (if licensed)
```

Government-record events **must** carry a `source_record_id`. The provenance chain is never lost: retraction hides an event from public view but preserves the row and its links.
