# data/ — ingestion working directories & fixtures

Only `data/fixtures/` is committed. `data/raw/` and `data/staging/` are
gitignored working directories that ingestion runs create and fill locally:

- `data/raw/` — immutable source snapshots exactly as downloaded (checksummed;
  mirrored to object storage so every sync run is reproducible). Real HCAD
  exports here contain owner names and mailing addresses — never commit,
  publish, or copy anything from this directory into fixtures.
- `data/staging/` — intermediate parsed/normalized artifacts between RAW and
  the canonical tables. Safe to delete at any time.

## Committed fixtures

### `hcad_sample/real_acct.txt`
Tab-delimited sample in the real HCAD `Real_acct_owner` column layout
(≈25 rows). The **format** matches the actual Harris County Appraisal District
export; the **rows are synthetic** so no licensing question attaches to the
repo. Used by `make import-hcad` and the HCAD adapter tests. A real sample can
be pulled with `scripts/fetch_hcad_sample.py` when download.hcad.org is
reachable.

### `houston_code_sample/records.json`
Code-enforcement records shaped like the City of Houston Open Data (CKAN)
`datastore_search` response. Used by `make import-houston-code` and the
Houston code-enforcement adapter tests.

Fixtures are the hermetic inputs for tests and demos — CI never touches live
government endpoints.
