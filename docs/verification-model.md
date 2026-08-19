# Verification Model

Knomes never says a bare "verified". Every ledger event carries exactly one of seven `VerificationLevel` values, and the UI renders the level (ProvenanceBadge) with a click-to-explain popover. Trust is graded, explicit, and traceable.

Related docs: [data-model.md](data-model.md) · [moderation.md](moderation.md) · [privacy.md](privacy.md)

## The seven levels

| Level | Display label | Meaning |
|---|---|---|
| GOVERNMENT_RECORD | Government Record | Taken directly from an official government source (HCAD, City of Houston code enforcement, permits). Always backed by a `source_record`. |
| LICENSED_PROFESSIONAL | Licensed Professional | Reported by a professional whose license Knomes has verified (`professionals.verification_status = VERIFIED`), e.g. an inspector's condition finding. |
| TRANSACTION_DOCUMENT | Transaction Document | Supported by a transaction document (termination notice, repair amendment, closing document) held as evidence. |
| DOCUMENT_SUPPORTED | Document Supported | Supported by a non-transaction document (invoice, receipt, contractor quote, photo) held as evidence. |
| PARTICIPANT_REPORTED | Participant Report | Asserted by a transaction participant (buyer, seller, agent) and reviewed, but without supporting documents. |
| SYSTEM_INFERRED | System Inference | Derived by Knomes from observable patterns (e.g. listing went Pending then Active again). Always labeled as inference, never dressed up as fact. |
| UNVERIFIED | Unverified | Recorded but not substantiated at any level above. |

## Confidence scores

`confidence` is an optional float on ledger events, orthogonal to the level: the level says *what kind* of backing exists, confidence says *how sure* the system is. Government-record events ingest at confidence 1.0. The canonical inference example seeds at 0.75. Property matching has its own confidence ladder (1.0 / 0.99 / 0.97 / ≥0.55 — see [ingestion.md](ingestion.md)); match confidence never upgrades an event's verification level.

## Claims vs. facts

A **claim** (`claims` table) is what a participant asserts: "the seller replaced the furnace". A **finding** (`findings`) is a reviewed property-condition fact. Claims enter with `review_status = PENDING_REVIEW` and become public only as findings after human review (`created_from_claim_id` preserves the link). Both sides of a dispute are retained: the demo's foundation dispute keeps the original CONDITION_FINDING *and* the engineer's counter-claim ("No repair recommended."), with the finding status `DISPUTED` — neither record is deleted.

## Causality rules

The system **never manufactures causality**:

- A listing going `Pending → Active` may produce `TRANSACTION_TERMINATED` with `outcome = TERMINATED`, `termination_reason = UNKNOWN`, `verification_level = SYSTEM_INFERRED` — and **never** `termination_reason = PROPERTY_CONDITION` without evidence. The public wording is exactly: *"A previous contract appears not to have closed. Reason not verified."*
- A termination reason of PROPERTY_CONDITION requires transaction-document or equivalent evidence (`transaction_cycles.evidence_id`), and `reason_verification_level` records what backs it.
- A permit near a finding is only ever a *candidate* resolution (`resolution_candidates`, status REVIEW_REQUIRED). The engine proposes, a human approves; candidates never mutate `finding.status`, and an HVAC permit can never close a ROOF finding (category→permit-type rule table).
- Finding statuses are OPEN, RESOLUTION_REPORTED, RESOLUTION_EVIDENCE_FOUND, RESOLVED, DISPUTED, SUPERSEDED, UNKNOWN — never "FIXED". "RESOLVED" is a reviewed state, not a promise about the house.

## No automated legal or engineering conclusions

Knomes does not generate legal conclusions (title status, lien validity, disclosure obligations) or engineering conclusions (structural adequacy, repair sufficiency). It records what licensed professionals and documents say, at the level that backs them. STRUCTURAL/FOUNDATION findings display professional statements verbatim with attribution to the level, never a Knomes judgment.

## The ten data-integrity invariants

1. Every government-record event has a `source_record_id` — no orphan GOVERNMENT_RECORD events.
2. The provenance chain is never lost: every public event traces to a source record or a reviewed submission with evidence.
3. Raw source records are retained exactly as received, deduplicated on `(source_name, source_record_id, content_hash)`.
4. A street address is never a permanent identifier; identity resolves HCAD account → parcel geometry → normalized address.
5. Every event's verification level is one of the seven enumerated values — never a bare "verified", never blank.
6. Pending→Active inference yields `TERMINATED` / `UNKNOWN` / `SYSTEM_INFERRED` only; `PROPERTY_CONDITION` requires evidence.
7. Machines propose, humans approve: resolution candidates and fuzzy matches never change finding status or attach events without review.
8. No hard deletes of history: corrections use `retracted_at` / `retraction_reason`, and mutations write `audit_log` rows with previous/new values.
9. Uploaded documents are private by default; only reviewed, derived findings become public. Documents live in object storage, never in Postgres.
10. Uncertain matches are never silently attached: below-threshold records go to the unmatched queue with `property_id NULL`.

These are enforced in code (constraints, service logic) and asserted by the test suite; any change that would weaken one is a spec change, not a refactor.
