# Moderation & Review

> **Status: design documented now; built in Phase 5.** Phases 0–3 create the queues (pending submissions, review-required matches, resolution candidates, disputes) and the audit trail; the `/admin/review` UI that works them comes later. Nothing in the MVP auto-publishes user content — the queues simply accumulate.

Related docs: [verification-model.md](verification-model.md) · [privacy.md](privacy.md) · [ingestion.md](ingestion.md)

## /admin/review design

One reviewer surface with four queues, each backed by existing tables:

| Queue | Backing rows | Reviewer decides |
|---|---|---|
| Submissions | `submissions` / `claims` with `PENDING_REVIEW` | Is this a publishable finding, and at what verification level? |
| Match review | `record_property_matches` with `REVIEW_REQUIRED` or `UNMATCHED` | Does this source record belong to this property? |
| Resolution candidates | `resolution_candidates` with `REVIEW_REQUIRED` | Does this event (e.g. a finalized permit) actually resolve this finding? |
| Disputes & corrections | findings in `DISPUTED`, correction requests | How do both sides get represented; does anything get retracted? |

Each queue item shows full provenance (source record, evidence, submitter role — never submitter identity in any public output) and the machine's stated reason (`match_reason`, `rule`, `match_score`).

## Reviewer actions

- **Submissions**: APPROVED (derive finding/events with redacted public text per the [privacy pipeline](privacy.md)), REJECTED, or NEEDS_CLARIFICATION. Approval sets the verification level the evidence actually earns — a licensed inspector's report with verified license → LICENSED_PROFESSIONAL; an undocumented buyer statement → PARTICIPANT_REPORTED.
- **Matches**: APPROVED (record attaches; deferred ledger events are then created) or REJECTED (back to unmatched); MANUAL match method for reviewer-assigned properties.
- **Resolution candidates**: APPROVED (creates a `finding_resolutions` row, links the event, advances finding status, e.g. → RESOLUTION_EVIDENCE_FOUND/RESOLVED) or REJECTED. The engine only ever proposes; status changes happen here.
- **Events**: retract (set `retracted_at` + `retraction_reason`) — never hard-delete, never silent edit.

## audit_log requirements

Every reviewer action writes an `audit_log` row: `actor_type`, `actor_id`, `action`, `entity_type`, `entity_id`, `previous_value`, `new_value` (JSONB), `timestamp`. Machine actions log too (e.g. resolution-candidate creation). The invariant: any public state must be reconstructible — who/what changed it, from what, to what, when. No moderation pathway may bypass the log.

## Correction requests

Anyone viewing a property page can request a correction. Requests carry a structured reason:

- **WRONG_PROPERTY** — event or record attached to the wrong parcel (match error).
- **FACTUAL_ERROR** — the underlying source or derived text is wrong.
- **OUTDATED** — status has changed and evidence exists (routes toward resolution flow).
- **DUPLICATE** — same real-world event appears twice (fix via `event_relationships SAME_AS` + retraction of one).
- **PRIVACY** — public text exposes personal information (expedited; see [privacy.md](privacy.md)).
- **OTHER** — free-text, triaged by a reviewer.

Corrections are resolved by retraction, re-match, or a new superseding event — the historical record is preserved either way.

## Dispute flow

Disputes attach to findings and never erase either side:

1. A party (often via a licensed professional) disputes a finding → `FINDING_DISPUTED` event; finding status → `DISPUTED`; the counter-claim is recorded (e.g. the demo's engineer statement "No repair recommended." against a foundation finding).
2. Both records remain visible with their own verification levels — the ledger shows the disagreement, Knomes does not adjudicate engineering questions ([verification-model.md](verification-model.md)).
3. A reviewer may later mark the finding `RESOLVED` (with evidence) or `SUPERSEDED` (a newer professional evaluation replaces it, linked via `event_relationships SUPERSEDES`); `FINDING_RESOLVED` / `FINDING_REOPENED` events record the transitions.
4. Every step is audit-logged; retraction is the only removal mechanism and requires a reason.
