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
| Fields consumed | `acct` → hcad_account_id; situs fields → address parts + normalized_address; `yr_impr` → year_built; `im_sq_ft` → building_sqft; `land_ar` → lot_sqft; `new_own_dt` (MM/DD/YYYY, may be blank) → OWNERSHIP_TRANSFER ledger events (date the current owner took title) |
| Known limitations | Appraisal snapshot, not real-time; situs address quality varies; contains no condition or transaction data; large tab-delimited files require streaming parse + `COPY` into `hcad_staging` |
| Adapter | `app/ingestion/hcad/` (download, parse, normalize, load, sync) |
| Last verified | 2026-08-19 |

HCAD is the **canonical parcel bootstrap**: it creates/updates `properties` keyed by `hcad_account_id`, the top of the identity hierarchy.

Since parser 1.2.0 the import also emits one public **OWNERSHIP_TRANSFER** ledger event per (property, `new_own_dt`): title fixed to "Ownership transferred", summary always empty, `verification_level=GOVERNMENT_RECORD`, confidence 1.0, `source_record_id` pointing at that row's own hcad `source_records` version. Owner names and mailing fields from the export are retained only in the private raw payload and never appear in public event text (privacy §37). Dedup is logical: an event is skipped when any OWNERSHIP_TRANSFER from any source already exists at the same (property, event date); a changed `new_own_dt` on re-import is a real subsequent sale — a new event is created for the new date and the old event is retained. Blank or malformed dates never reject the row; they are counted in the sync log as `ownership_dates_blank` / `ownership_dates_invalid`.

## HCAD — building details (Real_building_land.zip)

| | |
|---|---|
| Source | HCAD CAMA bulk export `Real_building_land.zip` (`settings.hcad_building_zip_url`, https://download.hcad.org/data/CAMA/2026/Real_building_land.zip, ~234 MB; 11 member files — layout verified against the live 2026 archive on 2026-08-19 via bounded HTTP range requests) |
| Owner | Harris Central Appraisal District (HCAD) |
| Acquisition method | Manual bulk file download; member files `building_res.txt` and `fixtures.txt` are extracted from the zip and passed to the importer via `--file` (kind auto-detected from the header) |
| License / usage terms | Public appraisal records published by HCAD for download and database import; no scraping involved |
| Refresh frequency | Annual CAMA export with interim updates; re-imports are idempotent (`IS DISTINCT FROM`-guarded set-based UPDATEs touch zero rows on an unchanged file) |
| Fields consumed | `building_res.txt` (tab-delimited, CRLF, header row, `acct` right-padded to 25 chars; one row per account+building — smallest `bld_num`, the primary improvement, wins): `qa_cd` → quality_code, `yr_remodel` → year_remodeled (`0`/blank = never remodeled → NULL). `fixtures.txt` (one row per account+building+fixture type): type `RMB` → bedrooms, `RMF` → bathrooms_full, `RMH` → bathrooms_half (decimal `units` summed across a property's buildings; other types ignored; a missing room type stays NULL, never a manufactured 0) |
| Known limitations | `date_erected` is present but not consumed (year_built already comes from real_acct `yr_impr`); the import never creates properties — accounts without a real_acct bootstrap are counted unmatched; no ledger events are emitted; garbage rows are rejected + counted without aborting |
| Adapter | `app/ingestion/hcad/building.py` — `python -m app.ingestion.hcad.building --file building_res.txt --file fixtures.txt` |
| Last verified | 2026-08-19 |

## HCAD — GIS parcels (Parcels.zip)

| | |
|---|---|
| Source | Harris Central Appraisal District public GIS downloads: https://download.hcad.org/data/GIS/Parcels.zip — zipped ESRI Shapefile of Harris County parcel polygons (205,129,702 bytes ≈ 196 MiB per HEAD request on 2026-08-19; Last-Modified Tue, 04 Aug 2026; no directory listing is exposed and dated variants such as `Parcels_2026_Jan.zip` 404) |
| Owner | Harris Central Appraisal District (HCAD) — download.hcad.org is an allowed host |
| Acquisition method | Manual download only; never fetched by tests or CI. Loaded by `scripts/load_hcad_parcels.py` (dockerized GDAL `ogr2ogr` → PostGIS); `settings.hcad_gis_parcels_url` exists (currently empty) if the team wants the URL configured, but the loader itself only reads local files |
| License / usage terms | HCAD public data; freely downloadable but no explicit open-data license is published with the file — review HCAD's data terms before redistributing, and never commit real parcel extracts to the repo (`data/raw` and `data/staging` are gitignored) |
| Refresh frequency | HCAD refreshes its public GIS downloads periodically (observed Last-Modified suggests at least monthly-to-quarterly); each download is a point-in-time snapshot; re-imports are idempotent, so refreshing is safe |
| Fields consumed | Account attribute (autodetected at runtime from the layer schema — candidates, in order: `HCAD_NUM`, `ACCOUNT`, `ACCT_NUM`, `ACCTNUM`, `ACCT`, `LOWPARCELI`, `LOWPARCELID`, `PARCEL_ID`, `PARCELID`; `--account-attr` overrides) + parcel polygon geometry → `properties.parcel_geometry` (MULTIPOLYGON, 4326) and latitude/longitude (ST_Centroid), joined on `btrim(account) = properties.hcad_account_id` (13-digit zero-padded strings) |
| Known limitations | Field names on the live file were NOT verified (the ~196 MB archive was not downloaded — the loader autodetects instead); stacked/condo parcels are deduplicated per account (largest area wins); invalid rings are repaired in PostGIS with `ST_MakeValid` |
| Adapter | `scripts/load_hcad_parcels.py` (see [ingestion.md](ingestion.md) for how to run it) |
| Last verified | 2026-08-19 |

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

## Houston Open Data — Project Level Actions (CKAN)

| | |
|---|---|
| Source | City of Houston Open Data portal, resource "Project Level Actions Since 2014" (`84a171f2-d601-4c79-bc4d-9733b378c663`, 696,184 rows as of 2026-08-19); pre-2014 sibling resource `58d4a211-6abe-4fa9-b12e-c065b81e2bbb` shares the schema and is pulled with `--pre-2014` |
| Owner | City of Houston |
| Acquisition method | CKAN `datastore_search` API on `data.houstontx.gov`; resource ids via `settings.houston_actions_resource_id` / `settings.houston_actions_pre2014_resource_id` |
| License / usage terms | Open government data published through the city's open-data portal API |
| Refresh frequency | API-accessible on demand; re-run the actions sync after a violations sync to pick up newly linkable projects (fully idempotent) |
| Fields consumed | Record shape verified live 2026-08-19: `{_id, NPPRJID, Sr_Request_Num, ViolationSubId, Action_Level, Action_Type, Action_Id (e.g. "PA371451"), Action_Date ("2014-01-07"), Action ("Close File", "Send Violation Letter", …), Comments}`. `Action_Id` → source_record_id (fallback `{NPPRJID}-{_id}`); `Action == "Close File"` → CODE_VIOLATION_RESOLVED at `Action_Date` ("Code violation case closed"); any other non-empty `Action` → CODE_VIOLATION_ACTION ("Code enforcement action: {Action}") — both GOVERNMENT_RECORD, confidence 1.0, summary always None |
| Known limitations | Action rows carry NO address and NO HCAD account — the only linkage is `NPPRJID`, joined to already-ingested `houston_code_enforcement` violations, so events exist only for records linkable via NPPRJID; a violation whose own match is only REVIEW_REQUIRED never transitively attaches actions (those go UNMATCHED with zero events); empty-`Action` rows (case notes) are retained as provenance with no event; `Comments` is inspector free text that can contain owner/tenant names — retained only inside raw_payload and NEVER used in a public event title or summary (`Action` itself is a system code-list value, safe for public titles) |
| Adapter | `app/ingestion/houston_actions/` (record_type `code_enforcement_action`, parser_version 1.0.0) — see [ingestion.md](ingestion.md) for the NPPRJID linkage mechanics |
| Last verified | 2026-08-19 |

## Houston Permits — weekly Permit Activity Report

**Finding: the City of Houston publishes NO per-address permit API.** CKAN (data.houstontx.gov) was re-enumerated via `package_search` on 2026-08-19: the only building-permit dataset is "City of Houston Residential Building Permits by Month and Year" (resource `c9cef716-4d81-4dac-9b05-d19acddf159f`, monthly aggregate XLS, 2004–present — no addresses); the remaining "permit" hits are stale 2012–2015 special-permit lists (dumpster, loading-zone, vehicle permits). The CIVICS portal (permits.houstontx.gov) and the Sold Permits Search web app are interactive apps, not bulk data — scraping them is forbidden. The compliant acquisition route, implemented below, is the Houston Permitting Center weekly Permit Activity Report ("Web eReport").

| | |
|---|---|
| Source | Houston Permitting Center weekly Permit Activity Report ("Web eReport") — one XLSX per week, single sheet; index page https://www.houstonpermittingcenter.org/sold-permits-search (officially published City of Houston report files; the same report is emailed every Monday as the "Permit e-Report", sign-up via https://www.houstonpermittingcenter.org/news-events). Some weeks are served at houstonpermittingcenter.org/media/NNNNN URLs (e.g. /media/11111 = Dec 1 2025) |
| Owner | City of Houston (Houston Permitting Center) |
| Acquisition method | Download the published weekly XLSX and run `python -m app.ingestion.houston_permits.sync --file PATH` (the primary operating mode — the weekly download URL changes every week, so `houston_permits_source_url` / `sync --url` must point at a specific published file). Accepts the .xlsx as published, or a .csv export. Layout verified against a real file downloaded 2026-08-19 (July 13–19, 453 data rows): rows 1–3 preamble ("Web eReport", "From: YYYY/MM/DD", "To : YYYY/MM/DD"), row 4 blank, row 5 header `Zip Code | Permit Date | Permit Type | Project No | Address | Comments`, then data rows, then a 3-line footer disclaimer |
| License / usage terms | City of Houston public report published for public dissemination; no license text on the file itself; data.houstontx.gov content is open data, and these report files are the city's designated public permit-transparency channel. The footer disclaimer (applicant-provided, unverified) is preserved in our provenance notes |
| Refresh frequency | Weekly (every Monday, covering the prior Mon–Sun). Coverage: new construction, remodeling, change-of-use, and demolition permits sold citywide |
| Fields consumed | Zip Code; Permit Date (YYYY/MM/DD — the date the permit was SOLD/issued that week) → PERMIT_ISSUED event date; Permit Type (observed codes: "Building Pmt", "Demolition", "OCC-BLDG PMT") → public title "Permit issued: {type}"; Project No (city permit/project number, stable unique id) → source_record_id; Address (situs street address, may include STE/FL/unit) → address ladder; Comments (applicant-provided project description — the footer states the City "does not confirm or verify" it) → raw_payload only |
| Known limitations | (1) Each file covers only its own week — historical backfill requires the archived pre-Dec-2025 report files (the Planning & Development archive; dev_reports.html stopped linking HPW permit reports effective 2025-12-01) or an Open Records request (https://www.houstonpermittingcenter.org/open-records, Open Records section 832-394-8800); the Sold Permits Search app itself only reaches back 3 years and may not be scraped. (2) No HCAD account column — records match through the EXACT_ADDRESS/alias/fuzzy address ladder; unit-suffixed commercial addresses often land in the unmatched/review queue. (3) No application or finalization dates — the format supports PERMIT_ISSUED events only; PERMIT_APPLIED/PERMIT_FINALIZED are never emitted. (4) Comments is applicant free text that can contain personal names — retained in raw_payload for provenance and never in public titles/summaries (public title = "Permit issued: {type}", summary = permit number + type code only) |
| Adapter | `app/ingestion/houston_permits/` (source_name `houston_permits`) |
| Last verified | 2026-08-19 |

## Houston 311 service requests (candidate future source — investigated, not implemented)

A current per-address, per-service-request dataset EXISTS as officially published City of Houston flat files (it is NOT on CKAN — CKAN only has the Lagan knowledge dump and 2012–2016 BARC files):

| | |
|---|---|
| Source | City of Houston 311 public data extracts; index https://houstontx.gov/311/servicerequestdata.html ("Data is updated monthly"), files hosted on hfdapp.houstontx.gov (city subdomain). Current extracts (pipe-delimited .txt, D365/CRIS era): MTD https://hfdapp.houstontx.gov/311/311-CRIS-Public-Data-Extract-D365-MTD-compressed.txt (verified 2026-08-19: 14.8 MB, extract timestamp same-day — refreshed at least daily despite the "monthly" label); full current file `311-CRIS-Public-Data-Extract-D365-compressed.txt`; per-year YTD files 2022–2025 (`…-YTD-compressed-YYYY.txt`). Legacy era: `311-Public-Data-Extract-2011.txt` … 2021, each with a "-clean" (piped) variant, plus a Harvey extract |
| Owner | City of Houston |
| Acquisition method | Officially published flat-file download (not implemented; no adapter yet) |
| License / usage terms | Officially published City of Houston public data files |
| Refresh frequency | Index says monthly; the MTD extract is refreshed at least daily |
| Fields consumed (candidate) | Verified fields (live header row): 365 Case Number, Case Number, Incident Address, Latitude, Longitude, Status, Created Date Local, Closed Date, Title, Incident Case Type, SLA fields, Council District, Department/Division, Incident Street/City/State, Zip Code, **TaxID (= HCAD account number — enables the HCAD_ID match rung at 1.0)**, Created Date UTC, neighborhood/routing fields, Channel, Latest Case Notes, Description, Resolution Notes. Closed Date IS present, so opened/closed events would both be supportable |
| Known limitations | 4 banner lines precede the header; pipe-delimited; citizen-authored free text in Description/Latest Case Notes/Resolution Notes can contain personal details — raw_payload-only if ever ingested. Most 311 categories are not property-condition signals; a future adapter should whitelist case types (e.g. sewer, flooding, dangerous building) rather than ingest all |
| Adapter | None yet (investigated 2026-08-19; candidate future source) |
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
