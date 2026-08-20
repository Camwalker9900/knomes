# Ingestion

All external data flows RAW → STAGING → CANONICAL → LEDGER through pluggable source adapters (diagram in [architecture.md](architecture.md)). Sources and their terms are cataloged in [data-sources.md](data-sources.md).

## SourceAdapter interface (`app/ingestion/base.py`)

```python
@dataclass
class RawSnapshot:
    source_name: str; storage_path: pathlib.Path; checksum: str
    retrieved_at: datetime; source_url: str | None

@dataclass
class EventCandidate:
    event_type: str; event_date: datetime; title: str; summary: str | None
    verification_level: str; confidence: float | None

@dataclass
class NormalizedRecord:
    record_type: str; source_record_id: str; raw_payload: dict
    normalized_address: str | None; hcad_account_id: str | None
    event_candidates: list[EventCandidate]; raw_address: str | None = None

class SourceAdapter(ABC):
    source_name: ClassVar[str]
    parser_version: ClassVar[str]
    async def fetch(self) -> RawSnapshot: ...
    def parse(self, snapshot: RawSnapshot) -> Iterator[dict]: ...
    def normalize(self, record: dict) -> NormalizedRecord: ...
```

Adapters are acquisition-agnostic: `fetch()` may download a bulk file, page a CKAN API, or read a manually dropped file — the rest of the pipeline is identical. `parse()` returns a streaming iterator (never whole-file-in-RAM); rejected rows are counted, not fatal.

## Runner pipeline (`app/ingestion/runner.py`)

`run_sync(adapter, session_factory, *, snapshot=None, resolve_property=None) -> SourceSyncRun`:

1. **fetch** — snapshot to `data/raw/` + object storage, checksummed (skippable by passing an existing `snapshot`, e.g. a fixture).
2. **parse → staging** — per-source staging where needed; HCAD makes ONE streaming pass with psycopg `COPY` into the UNLOGGED `hcad_staging` table (validated property fields + canonical JSON payload/content-hash + parsed `new_own_dt` per row) and then applies only set-based SQL: `INSERT … SELECT … ON CONFLICT (hcad_account_id) DO UPDATE` for properties (last file occurrence per account wins via a window function), one `INSERT … SELECT` for address aliases, one `INSERT … SELECT WHERE NOT EXISTS` (+`ON CONFLICT DO NOTHING` backstop) for `source_records`, one `UPDATE … FROM` linking provenance, and one `INSERT … SELECT` creating OWNERSHIP_TRANSFER events. No per-row ORM writes anywhere: a full-county 1.5M-row import measures ~12.5 min cold and ~4 min for an idempotent re-import (50k benchmark: ~3,500 rows/s cold, ~10,000 rows/s re-import).
3. **normalize** — `NormalizedRecord` per row; addresses go through the deterministic normalizer (`app/lib/address.py`).
4. **match** — the optional `resolve_property: Callable[[Session, NormalizedRecord], MatchResult | None]` hook is tried FIRST for every normalized record; returning `None` falls back to the standard `app/services/matching.match_record_to_property` ladder (below). Result stored in `record_property_matches` with `match_reason`.
5. **upsert source_records** — dedup on unique `(source_name, source_record_id, content_hash)`.
6. **reconcile** — ledger events created from `event_candidates` **for confidently matched records only**.
7. **metrics** — a `source_sync_runs` row closes the run.

## Reproducibility (`source_sync_runs`)

Every run records: source name, started/finished timestamps, snapshot storage path, snapshot **checksum**, source URL, **parser_version**, and counters (parsed / inserted / matched / unmatched / rejected / events_created). Given the snapshot and parser version, any run can be replayed and audited. Per-source freshness on the property page is derived from these rows.

## Matching ladder (`app/services/matching.py`)

`match_record_to_property(session, rec) -> MatchResult` (`property_id | None`, `method`, `confidence`, `reason`, `review_status`):

| Rung | Method | Confidence | Review status | Effect |
|---|---|---|---|---|
| 1 | HCAD_ID | 1.00 | AUTO_ACCEPTED | events created |
| 2 | EXACT_ADDRESS (normalized) | 0.99 | AUTO_ACCEPTED | events created |
| 3 | ADDRESS_ALIAS (property_addresses) | 0.97 | AUTO_ACCEPTED | events created |
| 4 | FUZZY_ADDRESS (trigram similarity ≥ 0.55) | similarity | REVIEW_REQUIRED | property_id stored, **events NOT created** until a human approves |
| 5 | — | — | UNMATCHED | `property_id NULL` → unmatched queue |

(`PARCEL_GEOMETRY` and `MANUAL` exist as `MatchMethod` values for geometry-based and reviewer-assigned matches.)

## Unmatched queue policy — never silently attach

A record that cannot be matched with high confidence is **never** attached to a property. It stays in `record_property_matches` with `review_status = UNMATCHED` (or `REVIEW_REQUIRED` for fuzzy hits) until a human resolves it in review ([moderation.md](moderation.md)). A wrong attach would put another house's violations on someone's timeline — the queue is the safety valve.

## Idempotency guarantees

Running the same import twice creates **zero** new rows: source records dedup on the content-hash triple, ledger events dedup on the partial unique `(property_id, event_type, event_date, source_record_id)`, and property upserts key on `hcad_account_id`. This is covered by `tests/test_runner_idempotency.py` and the per-adapter test suites. HCAD ownership events additionally dedup logically on (property_id, OWNERSHIP_TRANSFER, event_date) against events from ANY source, so refreshed payload versions never duplicate a transfer, while a changed `new_own_dt` creates a new event for the new date and retains the old one.

## Running imports

```sh
make import-hcad            # python -m app.ingestion.hcad.sync [--file PATH]
                            # bulk file from HCAD_DOWNLOAD_URL, or a local/fixture file;
                            # scripts/fetch_hcad_sample.py grabs a real sample manually
make import-houston-code    # CKAN datastore_search sync; needs HOUSTON_CODE_RESOURCE_ID
                            # (base URL HOUSTON_CKAN_BASE_URL, default data.houstontx.gov)

python -m app.ingestion.houston_actions.sync [--file PATH | --max-records N] [--pre-2014]
                            # CKAN "Project Level Actions" sync (live-mode flags cannot
                            # combine with --file); --pre-2014 pulls the pre-2014 sibling
                            # resource. Fixture: data/fixtures/houston_actions_sample/
                            # records.json (CKAN envelope, 11 synthetic actions)
python -m app.ingestion.houston_permits.sync --file PATH
                            # weekly Permit Activity Report (.xlsx as published, or a
                            # .csv export). --file is the PRIMARY operating mode: the
                            # weekly download URL changes every week, so
                            # houston_permits_source_url (or sync --url) must point at
                            # a specific published file
python -m app.ingestion.hcad.building --file building_res.txt --file fixtures.txt
                            # HCAD building details (members extracted from
                            # Real_building_land.zip; kind auto-detected from the header)
```

These run inside the api container when the compose stack is up, else locally via `uv`. Check the newest `source_sync_runs` row (counts + checksum) after each run.

The hcad sync log line now carries `ownership_events_created`, `ownership_dates_blank`, and `ownership_dates_invalid`; `source_sync_runs.events_created` counts the OWNERSHIP_TRANSFER events (previously always 0 for hcad). HCAD parser_version is 1.2.0.

### Houston actions — NPPRJID linkage and storage economy

Action rows carry no address and no HCAD account, so the actions sync uses the runner's `resolve_property` hook instead of the matching ladder: `NPPRJID` is joined to already-ingested `houston_code_enforcement` violations (`raw_payload->>'NPPRJID'`) and their AUTO_ACCEPTED `RecordPropertyMatch` is reused. The reused match is recorded as MatchMethod MANUAL, confidence 1.0, review_status AUTO_ACCEPTED, reason "linked via code-enforcement project NPPRJID {id}". A violation whose own match is only REVIEW_REQUIRED never transitively attaches actions — those actions go UNMATCHED to the review queue with zero events. A malformed or missing `Action_Date` on an action-bearing row raises ValueError → the record is counted rejected without aborting the run.

Storage economy: the resource holds ~696k rows, most on projects we never ingested. Before the runner sees anything, sync loads the set of known NPPRJIDs from ingested violations and the adapter's `parse` drops every action outside that set — filtered actions create NO source_records and NO queue rows. The full raw pull is still written to `data/raw/houston_actions/<timestamp>.json` as a checksummed snapshot for spec §7 reproducibility. Operational consequence: actions fetched before their violation project is ingested are skipped; re-run the actions sync after a violations sync to pick them up (fully idempotent).

### HCAD building details import

Streaming set-based `UPDATE … FROM (VALUES …)` keyed on `properties.hcad_account_id`, guarded by `IS DISTINCT FROM`: re-imports are idempotent and touch zero rows (`updated_at` only moves when a value changes). The import never creates properties (accounts without a real_acct bootstrap are counted unmatched), creates no ledger events, and rejects+counts garbage rows without aborting. Both members are sorted by `acct`; aggregation streams over contiguous account runs (a later reappearance wins, matching the real_acct last-wins rule). Synthetic fixture: `data/fixtures/hcad_sample/building_res_sample.txt` (real 2026 layout, CRLF, padded accts) — accounts align with `hcad_sample/real_acct.txt`; the fixtures.txt-member synthetic content lives inline in `apps/api/tests/test_hcad_building.py`.

### HCAD GIS parcels loader (`scripts/load_hcad_parcels.py`)

1. Manually download https://download.hcad.org/data/GIS/Parcels.zip (~196 MiB).
2. `cd apps/api && uv run python ../../scripts/load_hcad_parcels.py ~/Downloads/Parcels.zip` — accepts the zip (read via GDAL `/vsizip/`, no extraction), a directory containing the `.shp`, or the `.shp` path; `--database-url` defaults to the `DATABASE_URL` env var then the API `settings.database_url`.
3. Requirements: docker (GDAL runs from `ghcr.io/osgeo/gdal:alpine-small-latest` — no local GDAL install, no new Python deps) and the API uv env (psycopg) for the SQL step.

Pipeline: dockerized `ogrinfo` autodetects layer name + account attribute → dockerized `ogr2ogr` bulk-loads account + geometry into staging table `hcad_parcels_staging` (dropped/recreated via `-overwrite`; `-nlt PROMOTE_TO_MULTI -t_srs EPSG:4326 -dim XY`, COPY mode; DB host localhost is rewritten to `host.docker.internal` for the container, configurable via `--docker-host`) → one set-based UPDATE fills `properties.parcel_geometry` (MULTIPOLYGON, 4326) and latitude/longitude (ST_Centroid), joined on `btrim(account) = properties.hcad_account_id`, deduplicating stacked/condo parcels per account (largest area wins) and repairing invalid rings in PostGIS with `ST_MakeValid` (the alpine-small GDAL image has no GEOS, so `-makevalid` is unavailable at stage time) → staging dropped (`--keep-staging` to keep). Idempotent: updates apply only where the stored geometry `IS DISTINCT FROM` the staged one (or lat/lon are NULL); rerunning with the same file updates 0 rows. Flags: `--account-attr`, `--staging-table`, `--image`, `--docker-host`, `--keep-staging`. The SQL step (`apply_parcel_geometry` / `build_update_sql`) is importable without docker/ogr2ogr and is covered by `apps/api/tests/test_parcel_geometry.py`, including EXPLAIN proof that `ST_Contains` point queries use the `ix_properties_parcel_geometry` GIST index.

Optional: `settings.hcad_gis_parcels_url` (currently empty in `app/core/config.py`) can be set to https://download.hcad.org/data/GIS/Parcels.zip when the team wants the URL configured; the loader itself only reads local files and does not use this setting.
