# Data Sources

Knomes ingests **Houston / Harris County only**. Every source flows through the `SourceAdapter` pipeline described in [ingestion.md](ingestion.md) and lands with full provenance ([data-model.md](data-model.md)). Hard rule: **never scrape** HAR, Zillow, Realtor.com, Redfin, or any proprietary real-estate site — no exceptions, no "just this once".

## HCAD — parcel bootstrap

| | |
|---|---|
| Source | Harris Central Appraisal District public data downloads (bulk files, e.g. `Real_acct_owner.zip` / `real_acct.txt`) |
| Owner | Harris Central Appraisal District (HCAD) |
| Acquisition method | Bulk file download suitable for database import; `scripts/fetch_hcad_sample.py` pulls a sample manually; URL configured via `HCAD_DOWNLOAD_URL` |
| License / usage terms | Public appraisal records published by HCAD for download and database import; no scraping involved |
| Refresh frequency | HCAD publishes periodic bulk exports (certified annually, with interim updates); Knomes re-imports manually via `make import-hcad` |
| Fields consumed | `acct` → hcad_account_id; situs fields → address parts + normalized_address; `yr_impr` → year_built; `im_sq_ft` → building_sqft; `land_ar` → lot_sqft |
| Known limitations | Appraisal snapshot, not real-time; situs address quality varies; contains no condition or transaction data; large tab-delimited files require streaming parse + `COPY` into `hcad_staging` |
| Adapter | `app/ingestion/hcad/` (download, parse, normalize, load, sync) |
| Last verified | 2026-08-19 |

HCAD is the **canonical parcel bootstrap**: it creates/updates `properties` keyed by `hcad_account_id`, the top of the identity hierarchy.

## Houston Open Data — Building Code Enforcement (CKAN)

| | |
|---|---|
| Source | City of Houston Open Data portal, dataset "City of Houston Building Code Enforcement Violations (DON)", resource "All Code Enforcement Violations in FORMS Since 2014" (`1446a3ec-2633-4cf1-b15d-6dae9a07c4ed`, ~376k rows) |
| Owner | City of Houston |
| Acquisition method | CKAN `datastore_search` API with pagination via httpx; base URL `https://data.houstontx.gov`, resource id via `HOUSTON_CODE_RESOURCE_ID` |
| License / usage terms | Open government data published through the city's open-data portal API |
| Refresh frequency | API-accessible on demand; Knomes syncs via `make import-houston-code` (history back to 2014) |
| Fields consumed | `ViolationSubId` → source_record_id (fallback `NPPRJID`-`_id`); `HCAD` → hcad_account_id (HCAD_ID match rung at 1.0); `Merged_Situs` (+ `Zip` in payload) → address ladder; `RecordCreateDate` → CODE_VIOLATION_OPENED event date; `Violation_Category` → event title; `ShortDescription` (ordinance text) → event summary (500 chars); `Project_Status` retained in raw_payload |
| Known limitations | No reliable closure date on the violations resource, so resolution events are omitted (a `CLOSED` status without a date never fabricates CODE_VIOLATION_RESOLVED); `Comment311upd` free text may contain personal names and never surfaces in public titles/summaries; some `HCAD` fields are blank so those records fall back to address matching or the unmatched queue |
| Adapter | `app/ingestion/houston_code/` (adapter, normalize, sync) — emits CODE_VIOLATION_OPENED at GOVERNMENT_RECORD, confidence 1.0 |
| Last verified | 2026-08-19 |

## Houston Permits

| | |
|---|---|
| Source | City of Houston building permits |
| Owner | City of Houston |
| Acquisition method | **Under investigation.** The adapter interface is acquisition-agnostic and supports API, CSV export, bulk download, manual file drop, or a licensed feed. Scraping is **not** an option |
| License / usage terms | To be confirmed alongside the acquisition method |
| Refresh frequency | TBD once acquisition method is settled |
| Fields consumed (planned) | permit number, type (MECHANICAL/ELECTRICAL/PLUMBING/BUILDING…), status, dates, address → PERMIT_APPLIED / PERMIT_ISSUED / PERMIT_INSPECTION / PERMIT_FINALIZED events; permit types feed the resolution-candidate rule table |
| Known limitations | Not yet ingested; synthetic permit fixtures stand in for tests and the demo seed |
| Adapter | Interface ready via `SourceAdapter`; concrete adapter lands when acquisition is confirmed |
| Last verified | 2026-08-19 |

## Harris County Clerk — deeds and liens

| | |
|---|---|
| Source | Harris County Clerk real property records (deeds, liens, releases) |
| Owner | Harris County Clerk |
| Acquisition method | Bulk / FTP licensing to be investigated in Phase 2; **interface only** for now |
| License / usage terms | Bulk access is licensed; terms to be established before any ingestion |
| Refresh frequency | N/A until licensed |
| Fields consumed (planned) | instrument type/date/parties → OWNERSHIP_TRANSFER, DEED_RECORDED, LIEN_RECORDED, LIEN_RELEASED |
| Known limitations | No automation exists or is permitted until licensing is settled |
| Adapter | None yet; will implement `SourceAdapter` when licensed |
| Last verified | 2026-08-19 |

## MLS — HAR via Repliers (deferred)

| | |
|---|---|
| Source | Houston Association of Realtors MLS data, accessed via the Repliers API |
| Owner | HAR / MLS; Repliers as licensed API distributor |
| Acquisition method | Licensed API behind a `ListingProvider` interface; `MockListingProvider` ships first so listing logic is testable without MLS data. **Never scrape HAR, Zillow, Realtor.com, Redfin, or any proprietary listing site** |
| License / usage terms | Repliers API agreement (`REPLIERS_API_KEY`); deferred beyond Phases 0–3 |
| Refresh frequency | N/A until integrated |
| Fields consumed (planned) | listing lifecycle → LISTING_* events and transaction-cycle inference (Pending→Active ⇒ TERMINATED/UNKNOWN/SYSTEM_INFERRED — see [verification-model.md](verification-model.md)) |
| Known limitations | Deferred; all listing events in the MVP come from the synthetic seed |
| Adapter | `MockListingProvider` first; Repliers-backed provider later |
| Last verified | 2026-08-19 |

## Freshness

Each import writes a `source_sync_runs` row; the property page's sources footer and `GET /api/v1/properties/{id}/sources` report per-source record counts and `last_refreshed` so staleness is always visible.
