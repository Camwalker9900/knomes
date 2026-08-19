# Privacy

Knomes is **property-centric, not person-centric**. The product answers "what happened to this house?", never "what did this person do?". That principle drives every visibility rule below.

Related docs: [verification-model.md](verification-model.md) · [moderation.md](moderation.md) · [data-model.md](data-model.md)

## Three data classes

| Class | Examples | Handling |
|---|---|---|
| **PUBLIC** | Government records (HCAD parcel data, code enforcement, permits), reviewed findings, system inferences, timeline events with `visibility = PUBLIC` | Served by the anonymous read API; always with verification level and provenance |
| **PRIVATE SOURCE** | Raw source snapshots, staging rows, raw payloads (`source_records.raw_payload`), unmatched-queue records | Retained for provenance and reproducibility; never rendered directly to the public — only reviewed, derived events surface |
| **PRIVATE USER** | Uploaded documents (`evidence`), submitter identity and contact details (`submissions`), claims pending review | Private by default; identity is never made public at all |

`Visibility` is an explicit enum (`PUBLIC | PRIVATE | PENDING_REVIEW`) on events and evidence; timelines serve only `PUBLIC` rows with `retracted_at IS NULL`.

## Documents are private by default

Every uploaded document creates an `evidence` row with `visibility = PRIVATE` and a `storage_key` pointing at object storage — document bytes never enter PostgreSQL. What can become public is a **derived finding**: a reviewer reads the document, extracts the property-condition facts, and publishes those as findings/events at the appropriate verification level (see [moderation.md](moderation.md)). The inspection PDF itself stays private; "HVAC condenser at end of life — Licensed Professional" becomes public.

## Uploader identity is never public

Public findings attribute to a **role**, never a person: "Verified prospective buyer", "Licensed inspector", "Verified seller". The chain that proves who submitted what (`submissions` → contact fields, `professionals` → license records) exists for verification and moderation only. No name, email, license number, or other identifier of a submitter appears in any public payload, and API schemas simply have no fields for them.

## Property-centric, not person-centric

- Timelines describe events on a parcel, not the conduct of owners, buyers, or agents.
- Owner names from government sources are not a product surface; HCAD ingestion consumes parcel attributes (account, situs, year built, areas), not people.
- There are no user reviews, ratings, or reputation of individuals — the submission CTA is explicitly never "Write a review".
- Public summaries state observations, not blame: "A previous contract appears not to have closed. Reason not verified." names no one.

## PII pipeline

1. **Intake** — submission recorded with role + contact; documents land in object storage as PRIVATE evidence; nothing public yet (`PENDING_REVIEW`).
2. **Review** — a human reviewer verifies role (and license via `professionals` where claimed), then extracts property facts from the document.
3. **Redaction** — derived public text is written fresh by the reviewer: no names, addresses of individuals, phone numbers, emails, signatures, or license numbers of private parties are carried into public findings or summaries.
4. **Publication** — the finding/event goes public at its earned verification level, attributed by role; the underlying evidence stays private, linked for provenance.
5. **Correction** — privacy complaints go through the correction-request flow ([moderation.md](moderation.md)); offending public text is retracted (`retracted_at`), never silently edited, with an `audit_log` trail.

## Retention and deletion posture

History is never hard-deleted — retraction hides content from public view while preserving the ledger and audit trail. Private user data (contact details, documents) is retained only to support verification, disputes, and corrections, and is scoped away from every public read path by construction.
