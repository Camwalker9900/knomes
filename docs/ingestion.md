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

`run_sync(adapter, session_factory, *, snapshot=None) -> SourceSyncRun`:

1. **fetch** — snapshot to `data/raw/` + object storage, checksummed (skippable by passing an existing `snapshot`, e.g. a fixture).
2. **parse → staging** — per-source staging where needed; HCAD uses psycopg `COPY` into `hcad_staging` then a set-based upsert of `properties` keyed on `hcad_account_id`.
3. **normalize** — `NormalizedRecord` per row; addresses go through the deterministic normalizer (`app/lib/address.py`).
4. **match** — `app/services/matching.match_record_to_property` (ladder below); result stored in `record_property_matches` with `match_reason`.
5. **upsert source_records** — dedup on unique `(source_name, source_record_id, content_hash)`.
6. **reconcile** — ledger events created from `event_candidates` **for confidently matched records only**.
7. **metrics** — a `source_sync_runs` row closes the run.

## Reproducibility (`source_sync_runs`)

Every run records: source name, started/finished timestamps, snapshot storage path, snapshot **checksum**, source URL, **parser_version**, and counters (parsed / inserted / matched / unmatched / rejected). Given the snapshot and parser version, any run can be replayed and audited. Per-source freshness on the property page is derived from these rows.

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

Running the same import twice creates **zero** new rows: source records dedup on the content-hash triple, ledger events dedup on the partial unique `(property_id, event_type, event_date, source_record_id)`, and property upserts key on `hcad_account_id`. This is covered by `tests/test_runner_idempotency.py` and the per-adapter test suites.

## Running imports

```sh
make import-hcad            # python -m app.ingestion.hcad.sync [--file PATH]
                            # bulk file from HCAD_DOWNLOAD_URL, or a local/fixture file;
                            # scripts/fetch_hcad_sample.py grabs a real sample manually
make import-houston-code    # CKAN datastore_search sync; needs HOUSTON_CODE_RESOURCE_ID
                            # (base URL HOUSTON_CKAN_BASE_URL, default data.houstontx.gov)
```

Both run inside the api container when the compose stack is up, else locally via `uv`. Check the newest `source_sync_runs` row (counts + checksum) after each run.
