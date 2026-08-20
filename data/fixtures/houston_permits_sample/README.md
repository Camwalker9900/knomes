# houston_permits_sample

Synthetic fixture for the `houston_permits` adapter. Both files carry the same
12 permit rows; **all values are invented** ("EXAMPLESON" is a planted fake
surname used by tests to prove applicant `Comments` text never reaches a
public title or summary).

- `report.xlsx` — the layout of a real Houston Permitting Center weekly
  "Web eReport" permit activity report (verified 2026-08-19 from
  <https://www.houstonpermittingcenter.org/sold-permits-search>): preamble
  rows (`Web eReport` / `From:` / `To :`), blank row, header row
  `Zip Code | Permit Date | Permit Type | Project No | Address | Comments`,
  data rows stored as shared strings, then the footer disclaimer rows.
- `report.csv` — the same sheet as a CSV export, row for row.

Rows cover: matched address (plus a `STREET`→`ST` suffix variant), an in-file
exact duplicate and a revised re-import of the same permit number (logical
dedup), an unmatched address, a blank address, a malformed `Permit Date`
(rejected), and a permit type outside the observed set.

Import with:

```bash
cd apps/api
uv run python -m app.ingestion.houston_permits.sync --file ../../data/fixtures/houston_permits_sample/report.xlsx
```
