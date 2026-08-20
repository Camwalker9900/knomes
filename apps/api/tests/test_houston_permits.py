"""Houston building-permits adapter tests: parse, normalize mapping, fetch, end-to-end sync.

The fixture (data/fixtures/houston_permits_sample/report.xlsx, with report.csv
as the same sheet exported to CSV) replicates the verified layout of a real
Houston Permitting Center weekly "Web eReport" permit activity report —
preamble rows, the header ``Zip Code | Permit Date | Permit Type | Project No
| Address | Comments``, shared-string data rows, and footer disclaimer rows —
with synthetic values covering:

- two permits at 4210 BLUEBONNET ST (one written "BLUEBONNET STREET") that
  land on the same property via the EXACT_ADDRESS rung,
- an in-file exact duplicate and a revised re-import of permit 26030001
  (logical event dedup across content versions),
- unmatched addresses (queue rows), a blank address, a malformed Permit Date
  (rejected), and a permit type outside the observed set.

The report format carries only the sold (issue) date, so exactly one
PERMIT_ISSUED candidate per row — never PERMIT_APPLIED/PERMIT_FINALIZED — and
applicant-provided Comments (which may contain personal names; sentinel
"EXAMPLESON") must never surface in a public title or summary.
"""

from __future__ import annotations

import asyncio
import hashlib
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session

import app.ingestion.houston_permits.sync as sync_mod
from app.core.config import settings
from app.enums import EventType, MatchMethod, MatchReviewStatus, VerificationLevel, Visibility
from app.ingestion.houston_permits.adapter import HoustonPermitsAdapter
from app.ingestion.houston_permits.normalize import (
    normalize_houston_permit_record,
    parse_permit_date,
    permit_title,
)
from app.ingestion.houston_permits.parse import iter_report_rows
from app.ingestion.houston_permits.sync import build_snapshot_from_file
from app.ingestion.runner import run_sync
from app.lib.address import address_hash, normalize_address
from app.models import LedgerEvent, Property, RecordPropertyMatch, SourceRecord, SourceSyncRun

SOURCE_NAME = "houston_permits"
FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "houston_permits_sample"
XLSX_FIXTURE = FIXTURE_DIR / "report.xlsx"
CSV_FIXTURE = FIXTURE_DIR / "report.csv"

COMMENT_SENTINEL = "EXAMPLESON"  # fake surname planted in applicant Comments

MATCHED_PERMIT = "26030001"  # Building Pmt at 4210 BLUEBONNET ST (x3 rows)
SUFFIX_PERMIT = "26030002"  # Demolition at "4210 BLUEBONNET STREET"
UNMATCHED_PERMIT = "26030003"  # address matches nothing
MALFORMED_PERMIT = "26030004"  # Permit Date "N/A" -> rejected
OCC_PERMIT = "26030005"  # OCC-BLDG PMT
BLANK_ADDRESS_PERMIT = "26030006"  # Address cell omitted from the sheet
UNKNOWN_TYPE_PERMIT = "26030010"  # "Sign Bldg Pmt" -> raw type passes through

# (event_type, event_date, title, Project No) in strict chronological order.
EXPECTED_TIMELINE = [
    (
        EventType.PERMIT_ISSUED,
        datetime(2026, 7, 13, tzinfo=UTC),
        "Permit issued: Building permit",
        MATCHED_PERMIT,
    ),
    (
        EventType.PERMIT_ISSUED,
        datetime(2026, 7, 14, tzinfo=UTC),
        "Permit issued: Demolition permit",
        SUFFIX_PERMIT,
    ),
]


def _fixture_rows() -> list[dict[str, Any]]:
    return list(iter_report_rows(XLSX_FIXTURE))


def _rows_by_permit() -> dict[str, dict[str, Any]]:
    """First fixture row per Project No (26030001 appears three times)."""
    rows: dict[str, dict[str, Any]] = {}
    for row in _fixture_rows():
        rows.setdefault(row["Project No"], row)
    return rows


@pytest.fixture()
def run_session_factory(db: Session) -> Callable[[], Session]:
    """Session factory sharing the test connection, so runner commits become savepoints."""
    bind = db.get_bind()
    assert isinstance(bind, Connection)

    def _factory() -> Session:
        return Session(
            bind=bind,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    return _factory


def _make_property(db: Session) -> Property:
    normalized = normalize_address("4210 Bluebonnet St")
    prop = Property(
        address_line1="4210 Bluebonnet St",
        city="Houston",
        state="TX",
        postal_code="77025",
        normalized_address=normalized,
        address_hash=address_hash(normalized),
    )
    db.add(prop)
    db.flush()
    return prop


# ---------------------------------------------------------------------------
# parse: report structure (no database needed)
# ---------------------------------------------------------------------------


def test_parse_xlsx_skips_preamble_and_footer_and_yields_data_rows() -> None:
    rows = _fixture_rows()

    assert len(rows) == 12  # preamble, header, and footer rows never surface
    assert all(
        set(row) == {"Zip Code", "Permit Date", "Permit Type", "Project No", "Address", "Comments"}
        for row in rows
    )
    first = rows[0]
    assert first["Zip Code"] == "77025"
    assert first["Permit Date"] == "2026/07/13"
    assert first["Permit Type"] == "Building Pmt"
    assert first["Project No"] == MATCHED_PERMIT
    assert first["Address"] == "4210 BLUEBONNET ST"
    # No row is a footer disclaimer fragment.
    assert not any("Deed Restriction" in (row["Zip Code"] or "") for row in rows)


def test_parse_csv_export_yields_identical_rows() -> None:
    assert list(iter_report_rows(CSV_FIXTURE)) == _fixture_rows()


def test_parse_blank_address_cell_becomes_none() -> None:
    row = _rows_by_permit()[BLANK_ADDRESS_PERMIT]
    assert row["Address"] is None


def test_parse_supports_inline_string_cells(tmp_path: Path) -> None:
    """Producers that write inlineStr cells (no sharedStrings part) still parse."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    header = ["Zip Code", "Permit Date", "Permit Type", "Project No", "Address", "Comments"]
    data = ["77002", "2026/07/13", "Building Pmt", "26099999", "1619 FANNIN ST", "REMODEL"]

    def row_xml(r: int, values: list[str]) -> str:
        cells = "".join(
            f'<c r="{chr(ord("A") + i)}{r}" t="inlineStr"><is><t>{v}</t></is></c>'
            for i, v in enumerate(values)
        )
        return f'<row r="{r}">{cells}</row>'

    sheet = (
        f'<worksheet xmlns="{ns}"><sheetData>'
        f"{row_xml(1, header)}{row_xml(2, data)}"
        "</sheetData></worksheet>"
    )
    path = tmp_path / "inline.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)

    rows = list(iter_report_rows(path))
    assert rows == [dict(zip(header, data, strict=True))]


def test_parse_rejects_file_without_header_row(tmp_path: Path) -> None:
    bogus = tmp_path / "not_a_report.csv"
    bogus.write_text("just,some,cells\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no header row"):
        list(iter_report_rows(bogus))


def test_parse_rejects_unknown_file_type(tmp_path: Path) -> None:
    other = tmp_path / "report.pdf"
    other.write_bytes(b"%PDF-")
    with pytest.raises(ValueError, match="unsupported report file type"):
        list(iter_report_rows(other))


# ---------------------------------------------------------------------------
# normalize: field mapping (no database needed)
# ---------------------------------------------------------------------------


def test_normalize_maps_permit_fields() -> None:
    rec = normalize_houston_permit_record(_rows_by_permit()[MATCHED_PERMIT])

    assert rec.record_type == "building_permit"
    assert rec.source_record_id == MATCHED_PERMIT
    assert rec.hcad_account_id is None  # the report has no HCAD column
    assert rec.raw_address == "4210 BLUEBONNET ST"
    assert rec.normalized_address == "4210 BLUEBONNET ST"
    # Zip and Comments stay visible in the payload for provenance.
    assert rec.raw_payload["Zip Code"] == "77025"
    assert COMMENT_SENTINEL in rec.raw_payload["Comments"]

    assert len(rec.event_candidates) == 1
    issued = rec.event_candidates[0]
    assert issued.event_type == EventType.PERMIT_ISSUED
    assert issued.event_date == datetime(2026, 7, 13, tzinfo=UTC)
    assert issued.title == "Permit issued: Building permit"
    assert issued.summary == (
        "Permit no. 26030001 (Building Pmt) — City of Houston weekly permit activity report."
    )
    assert issued.verification_level == VerificationLevel.GOVERNMENT_RECORD
    assert issued.confidence == 1.0


def test_normalize_street_suffix_variant_normalizes_to_same_address() -> None:
    rec = normalize_houston_permit_record(_rows_by_permit()[SUFFIX_PERMIT])
    assert rec.raw_address == "4210 BLUEBONNET STREET"
    assert rec.normalized_address == "4210 BLUEBONNET ST"


def test_normalize_emits_only_permit_issued_never_applied_or_finalized() -> None:
    """The weekly report carries only the sold date; other dates would be fabricated."""
    for row in _fixture_rows():
        try:
            rec = normalize_houston_permit_record(row)
        except ValueError:
            continue
        assert [c.event_type for c in rec.event_candidates] == [EventType.PERMIT_ISSUED]


def test_normalize_never_puts_comments_in_title_or_summary() -> None:
    saw_comment = False
    for row in _fixture_rows():
        try:
            rec = normalize_houston_permit_record(row)
        except ValueError:
            continue
        comment = row.get("Comments")
        for candidate in rec.event_candidates:
            text = candidate.title + " " + (candidate.summary or "")
            assert COMMENT_SENTINEL not in text
            if comment:
                saw_comment = True
                assert comment not in text
    assert saw_comment  # the fixture must keep exercising this


def test_normalize_title_labels_known_types_and_passes_unknown_through() -> None:
    assert permit_title("Building Pmt") == "Permit issued: Building permit"
    assert permit_title("Demolition") == "Permit issued: Demolition permit"
    assert permit_title("OCC-BLDG PMT") == "Permit issued: Occupancy building permit"
    assert permit_title(None) == "Permit issued"
    # A code outside the observed set is never assigned an invented meaning.
    rec = normalize_houston_permit_record(_rows_by_permit()[UNKNOWN_TYPE_PERMIT])
    assert rec.event_candidates[0].title == "Permit issued: Sign Bldg Pmt"


def test_normalize_blank_address_yields_none_address() -> None:
    rec = normalize_houston_permit_record(_rows_by_permit()[BLANK_ADDRESS_PERMIT])
    assert rec.raw_address is None
    assert rec.normalized_address is None


def test_normalize_rejects_malformed_permit_date() -> None:
    with pytest.raises(ValueError, match="Permit Date"):
        normalize_houston_permit_record(_rows_by_permit()[MALFORMED_PERMIT])


def test_normalize_requires_permit_date() -> None:
    row = dict(_rows_by_permit()[MATCHED_PERMIT], **{"Permit Date": None})
    with pytest.raises(ValueError, match="Permit Date"):
        normalize_houston_permit_record(row)


def test_normalize_requires_project_no() -> None:
    row = dict(_rows_by_permit()[MATCHED_PERMIT], **{"Project No": None})
    with pytest.raises(ValueError, match="Project No"):
        normalize_houston_permit_record(row)


def test_normalize_keeps_unsafe_permit_number_out_of_public_summary() -> None:
    row = dict(_rows_by_permit()[MATCHED_PERMIT], **{"Project No": "SEE J. EXAMPLESON"})
    rec = normalize_houston_permit_record(row)
    summary = rec.event_candidates[0].summary
    assert summary == "City of Houston weekly permit activity report."
    assert COMMENT_SENTINEL not in (rec.event_candidates[0].title + " " + summary)


def test_parse_permit_date_accepts_report_iso_and_excel_serial_variants() -> None:
    expected = datetime(2026, 7, 13, tzinfo=UTC)
    for text in ("2026/07/13", "2026-07-13", "2026-07-13T00:00:00+00:00"):
        assert parse_permit_date(text) == expected
    # Excel serial 46216 == 2026-07-13 (epoch 1899-12-30).
    assert parse_permit_date("46216") == expected
    assert parse_permit_date("46216.0") == expected
    assert parse_permit_date(None) is None
    assert parse_permit_date("   ") is None
    with pytest.raises(ValueError, match="Permit Date"):
        parse_permit_date("N/A")


# ---------------------------------------------------------------------------
# adapter: fetch
# ---------------------------------------------------------------------------


def test_fetch_without_url_tells_operator_to_use_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "houston_permits_source_url", "")
    with pytest.raises(ValueError, match="--file"):
        asyncio.run(HoustonPermitsAdapter().fetch())


def test_fetch_downloads_report_and_writes_checksummed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "houston_permits_source_url", "")
    report_bytes = XLSX_FIXTURE.read_bytes()
    url = "https://www.houstonpermittingcenter.org/sites/g/files/x/2026-07/July%2013-19.xlsx"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        return httpx.Response(200, content=report_bytes)

    adapter = HoustonPermitsAdapter(
        source_url=url, raw_dir=tmp_path, transport=httpx.MockTransport(handler)
    )
    snapshot = asyncio.run(adapter.fetch())

    assert snapshot.source_name == SOURCE_NAME
    assert snapshot.storage_path.parent == tmp_path
    assert snapshot.storage_path.suffix == ".xlsx"
    assert snapshot.source_url == url
    assert snapshot.checksum == hashlib.sha256(report_bytes).hexdigest()
    assert snapshot.storage_path.read_bytes() == report_bytes
    # The snapshot round-trips through parse.
    assert len(list(adapter.parse(snapshot))) == 12


def test_fetch_constructor_url_overrides_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings, "houston_permits_source_url", "https://example.invalid/settings.xlsx"
    )
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=CSV_FIXTURE.read_bytes())

    adapter = HoustonPermitsAdapter(
        source_url="https://www.houstonpermittingcenter.org/media/9999/report.csv",
        raw_dir=tmp_path,
        transport=httpx.MockTransport(handler),
    )
    snapshot = asyncio.run(adapter.fetch())
    assert seen == ["https://www.houstonpermittingcenter.org/media/9999/report.csv"]
    assert snapshot.storage_path.suffix == ".csv"
    assert len(list(adapter.parse(snapshot))) == 12


# ---------------------------------------------------------------------------
# sync CLI
# ---------------------------------------------------------------------------


def test_cli_file_and_url_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        sync_mod.main(["--file", str(XLSX_FIXTURE), "--url", "https://example.com/r.xlsx"])


def test_cli_requires_some_source_when_url_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "houston_permits_source_url", "")
    with pytest.raises(SystemExit):
        sync_mod.main([])


def test_cli_rejects_missing_file() -> None:
    with pytest.raises(SystemExit):
        sync_mod.main(["--file", "/nonexistent/report.xlsx"])


def test_cli_passes_url_through_to_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "houston_permits_source_url", "")
    captured: dict[str, Any] = {}

    def fake_run_sync(adapter: Any, session_factory: Any, *, snapshot: Any = None) -> Any:
        captured["adapter"] = adapter
        captured["snapshot"] = snapshot
        return SimpleNamespace(
            id="00000000-0000-0000-0000-000000000000",
            status="SUCCEEDED",
            records_parsed=0,
            records_matched=0,
            records_unmatched=0,
            records_rejected=0,
            source_records_created=0,
            events_created=0,
            snapshot_path=None,
            error_message=None,
        )

    monkeypatch.setattr(sync_mod, "run_sync", fake_run_sync)
    exit_code = sync_mod.main(["--url", "https://example.com/week.xlsx"])

    assert exit_code == 0
    assert captured["snapshot"] is None  # live mode
    adapter = captured["adapter"]
    assert isinstance(adapter, HoustonPermitsAdapter)
    assert adapter._resolved_url() == "https://example.com/week.xlsx"


# ---------------------------------------------------------------------------
# end to end: fixture sync through the shared runner
# ---------------------------------------------------------------------------


def test_fixture_sync_end_to_end_and_idempotent(
    db: Session, run_session_factory: Callable[[], Session]
) -> None:
    prop = _make_property(db)
    db.commit()

    adapter = HoustonPermitsAdapter()
    snapshot = build_snapshot_from_file(XLSX_FIXTURE)
    run1 = run_sync(adapter, run_session_factory, snapshot=snapshot)

    # --- run metrics: malformed-date row rejected, run still SUCCEEDED ---
    assert run1.status == "SUCCEEDED"
    assert run1.error_message is None
    assert run1.records_parsed == 12
    assert run1.records_matched == 4  # 3x 26030001 + the STREET-suffix demolition
    assert run1.records_unmatched == 7
    assert run1.records_rejected == 1
    # 26030001 appears three times: exact duplicate dedupes on content hash,
    # the revised comment creates a second content version of the same record.
    assert run1.source_records_created == 10
    assert run1.events_created == 2  # logical dedup: one event per (permit, date)
    assert run1.parser_version == "1.0.0"
    assert run1.snapshot_path == str(XLSX_FIXTURE.resolve())
    assert run1.snapshot_checksum == snapshot.checksum

    source_records = (
        db.execute(select(SourceRecord).where(SourceRecord.source_name == SOURCE_NAME))
        .scalars()
        .all()
    )
    assert len(source_records) == 10
    by_permit: dict[str, list[SourceRecord]] = {}
    for sr in source_records:
        by_permit.setdefault(sr.source_record_id, []).append(sr)
    assert len(by_permit) == 9  # distinct permit numbers
    assert len(by_permit[MATCHED_PERMIT]) == 2  # two content versions
    assert MALFORMED_PERMIT not in by_permit  # rejected before any write
    assert all(sr.record_type == "building_permit" for sr in source_records)

    # --- matched permits: one ISSUED event each, chronological, full provenance ---
    events = (
        db.execute(
            select(LedgerEvent)
            .where(LedgerEvent.property_id == prop.id)
            .order_by(LedgerEvent.event_date)
        )
        .scalars()
        .all()
    )
    assert [
        (e.event_type, e.event_date, e.title, _permit_no_of(by_permit, e)) for e in events
    ] == EXPECTED_TIMELINE
    for event in events:
        assert event.source_record_id is not None
        assert event.verification_level == VerificationLevel.GOVERNMENT_RECORD
        assert event.confidence == 1.0
        assert event.visibility == Visibility.PUBLIC

    # --- only PERMIT_ISSUED ever materializes from this format ---
    all_ids = [sr.id for sr in source_records]
    non_issued = db.execute(
        select(func.count())
        .select_from(LedgerEvent)
        .where(
            LedgerEvent.source_record_id.in_(all_ids),
            LedgerEvent.event_type != EventType.PERMIT_ISSUED,
        )
    ).scalar_one()
    assert non_issued == 0
    total_events = db.execute(
        select(func.count())
        .select_from(LedgerEvent)
        .where(LedgerEvent.source_record_id.in_(all_ids))
    ).scalar_one()
    assert total_events == 2

    # --- applicant Comments (may contain names) never surface publicly ---
    fixture_comments = [row["Comments"] for row in _fixture_rows() if row["Comments"]]
    assert fixture_comments  # the fixture must keep planting comments
    public_events = (
        db.execute(select(LedgerEvent).where(LedgerEvent.source_record_id.in_(all_ids)))
        .scalars()
        .all()
    )
    for event in public_events:
        text = event.title + " " + (event.summary or "")
        assert COMMENT_SENTINEL not in text
        for comment in fixture_comments:
            assert comment not in text
    # ...but they remain available as private provenance in raw_payload.
    assert any(
        COMMENT_SENTINEL in (sr.raw_payload.get("Comments") or "")
        for sr in by_permit[MATCHED_PERMIT]
    )

    # --- both matched permits rode the EXACT_ADDRESS rung (no HCAD ids here) ---
    for permit_no in (MATCHED_PERMIT, SUFFIX_PERMIT):
        for sr in by_permit[permit_no]:
            match = db.execute(
                select(RecordPropertyMatch).where(RecordPropertyMatch.source_record_id == sr.id)
            ).scalar_one()
            assert match.property_id == prop.id
            assert match.match_method == MatchMethod.EXACT_ADDRESS
            assert match.confidence == 0.99
            assert match.review_status == MatchReviewStatus.AUTO_ACCEPTED
            assert sr.property_id == prop.id

    # --- unmatched permits: queue rows with property_id None and ZERO events ---
    for permit_no in (UNMATCHED_PERMIT, OCC_PERMIT, BLANK_ADDRESS_PERMIT):
        (unmatched_sr,) = by_permit[permit_no]
        match = db.execute(
            select(RecordPropertyMatch).where(
                RecordPropertyMatch.source_record_id == unmatched_sr.id
            )
        ).scalar_one()
        assert match.property_id is None
        assert match.match_method is None
        assert match.review_status == MatchReviewStatus.UNMATCHED
        assert match.match_reason
        assert unmatched_sr.property_id is None  # never silently attach
        assert (
            db.execute(
                select(func.count())
                .select_from(LedgerEvent)
                .where(LedgerEvent.source_record_id == unmatched_sr.id)
            ).scalar_one()
            == 0
        )

    # --- re-run with the same snapshot: zero new source_records/events ---
    run2 = run_sync(HoustonPermitsAdapter(), run_session_factory, snapshot=snapshot)
    assert run2.status == "SUCCEEDED"
    assert run2.records_parsed == 12
    assert run2.records_matched == 4
    assert run2.records_unmatched == 7
    assert run2.records_rejected == 1
    assert run2.source_records_created == 0
    assert run2.events_created == 0

    assert (
        db.execute(
            select(func.count())
            .select_from(SourceRecord)
            .where(SourceRecord.source_name == SOURCE_NAME)
        ).scalar_one()
        == 10
    )
    assert (
        db.execute(
            select(func.count()).select_from(LedgerEvent).where(LedgerEvent.property_id == prop.id)
        ).scalar_one()
        == 2
    )

    # --- SourceSyncRun rows persisted with counters populated ---
    runs = (
        db.execute(select(SourceSyncRun).where(SourceSyncRun.source_name == SOURCE_NAME))
        .scalars()
        .all()
    )
    assert {run.id for run in runs} == {run1.id, run2.id}
    for run in runs:
        assert run.status == "SUCCEEDED"
        assert run.started_at is not None
        assert run.finished_at is not None
        assert run.records_parsed == 12
        assert run.records_rejected == 1
        assert run.parser_version == "1.0.0"
        assert run.snapshot_path == str(XLSX_FIXTURE.resolve())
        assert run.snapshot_checksum == snapshot.checksum


def test_csv_export_sync_is_equivalent_and_idempotent_with_xlsx(
    db: Session, run_session_factory: Callable[[], Session]
) -> None:
    """The CSV export of the same report yields the same records — and importing
    it after the XLSX creates zero new rows (same logical records)."""
    prop = _make_property(db)
    db.commit()

    run_xlsx = run_sync(
        HoustonPermitsAdapter(),
        run_session_factory,
        snapshot=build_snapshot_from_file(XLSX_FIXTURE),
    )
    assert run_xlsx.events_created == 2

    run_csv = run_sync(
        HoustonPermitsAdapter(),
        run_session_factory,
        snapshot=build_snapshot_from_file(CSV_FIXTURE),
    )
    assert run_csv.status == "SUCCEEDED"
    assert run_csv.records_parsed == 12
    assert run_csv.source_records_created == 0
    assert run_csv.events_created == 0
    assert (
        db.execute(
            select(func.count()).select_from(LedgerEvent).where(LedgerEvent.property_id == prop.id)
        ).scalar_one()
        == 2
    )


def _permit_no_of(by_permit: dict[str, list[SourceRecord]], event: LedgerEvent) -> str:
    """Resolve a ledger event back to the Project No of its source record."""
    for permit_no, source_records in by_permit.items():
        if any(sr.id == event.source_record_id for sr in source_records):
            return permit_no
    raise AssertionError(f"event {event.id} has no source record in this sync")
