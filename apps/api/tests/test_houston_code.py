"""Houston code-enforcement adapter tests: normalize mapping, CKAN fetch, end-to-end sync.

The fixture (data/fixtures/houston_code_sample/records.json) is shaped exactly
like a CKAN ``datastore_search`` response for resource
1446a3ec-2633-4cf1-b15d-6dae9a07c4ed ("All Code Enforcement Violations in FORMS
Since 2014") — real field names, synthetic values — and contains:

- two violations carrying HCAD account 0450230000012 at 1234 WESTHEIMER RD
  (matching ladder's HCAD_ID rung at confidence 1.0),
- one violation with a blank HCAD whose address normalizes to the same
  property (falls through to the EXACT_ADDRESS rung),
- one blank-HCAD record whose address matches nothing (unmatched queue),
- one record with a malformed RecordCreateDate (rejected),
- ten filler records with fake HCAD accounts and addresses matching nothing.

The resource has no closure-date field, so a CLOSED Project_Status must never
manufacture a CODE_VIOLATION_RESOLVED event, and Comment311upd (free text that
may contain names) must never surface in a public title or summary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session

import app.ingestion.houston_code.sync as sync_mod
from app.core.config import settings
from app.enums import EventType, MatchMethod, MatchReviewStatus, VerificationLevel, Visibility
from app.ingestion.houston_code.adapter import HoustonCodeAdapter
from app.ingestion.houston_code.normalize import (
    normalize_houston_code_record,
    parse_ckan_datetime,
)
from app.ingestion.houston_code.sync import build_snapshot_from_file
from app.ingestion.runner import run_sync
from app.lib.address import address_hash, normalize_address
from app.models import LedgerEvent, Property, RecordPropertyMatch, SourceRecord, SourceSyncRun

SOURCE_NAME = "houston_code_enforcement"
FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "houston_code_sample"
    / "records.json"
)

HCAD_ACCOUNT = "0450230000012"
COMMENT_SENTINEL = "EXAMPLESON"  # fake surname planted in Comment311upd fields

CLOSED_VIOLATION = "910001"  # HCAD rung; Project_Status CLOSED, no closure date
OPEN_VIOLATION = "910002"  # HCAD rung; Project_Status OPEN
ADDRESS_VIOLATION = "910003"  # blank HCAD -> EXACT_ADDRESS rung
UNMATCHED_VIOLATION = "910004"  # blank HCAD, address matches nothing
MALFORMED_VIOLATION = "910005"  # malformed RecordCreateDate -> rejected
NO_CATEGORY_VIOLATION = "910006"  # null Violation_Category/ShortDescription
COMPOSITE_ID_RECORD = 8  # _id of the record with null ViolationSubId

# (event_type, event_date, title, ViolationSubId) in strict chronological order.
EXPECTED_TIMELINE = [
    (
        EventType.CODE_VIOLATION_OPENED,
        datetime(2024, 11, 5, tzinfo=UTC),
        "Code violation opened: Dangerous Building",
        CLOSED_VIOLATION,
    ),
    (
        EventType.CODE_VIOLATION_OPENED,
        datetime(2025, 3, 2, tzinfo=UTC),
        "Code violation opened: Junked Motor Vehicle",
        OPEN_VIOLATION,
    ),
    (
        EventType.CODE_VIOLATION_OPENED,
        datetime(2025, 5, 10, tzinfo=UTC),
        "Code violation opened: Nuisance - High Weeds",
        ADDRESS_VIOLATION,
    ),
]


def _fixture_record_list() -> list[dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = payload["result"]["records"]
    return records


def _fixture_records() -> dict[str, dict[str, Any]]:
    """Fixture records keyed by ViolationSubId (records that carry one)."""
    return {
        record["ViolationSubId"]: record
        for record in _fixture_record_list()
        if record.get("ViolationSubId")
    }


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
    normalized = normalize_address("1234 Westheimer Rd")
    prop = Property(
        hcad_account_id=HCAD_ACCOUNT,
        address_line1="1234 Westheimer Rd",
        city="Houston",
        state="TX",
        postal_code="77006",
        normalized_address=normalized,
        address_hash=address_hash(normalized),
    )
    db.add(prop)
    db.flush()
    return prop


# ---------------------------------------------------------------------------
# normalize: field mapping (no database needed)
# ---------------------------------------------------------------------------


def test_normalize_maps_violation_fields() -> None:
    rec = normalize_houston_code_record(_fixture_records()[OPEN_VIOLATION])

    assert rec.record_type == "code_enforcement_violation"
    assert rec.source_record_id == OPEN_VIOLATION
    assert rec.hcad_account_id == HCAD_ACCOUNT
    assert rec.raw_address == "1234 WESTHEIMER RD"
    assert rec.normalized_address == "1234 WESTHEIMER RD"
    # Zip and Project_Status stay visible in the payload for provenance.
    assert rec.raw_payload["Zip"] == "77006"
    assert rec.raw_payload["Project_Status"] == "OPEN"

    assert len(rec.event_candidates) == 1
    opened = rec.event_candidates[0]
    assert opened.event_type == EventType.CODE_VIOLATION_OPENED
    assert opened.event_date == datetime(2025, 3, 2, tzinfo=UTC)
    assert opened.title == "Code violation opened: Junked Motor Vehicle"
    assert opened.summary == "Junk Motor Vehicle"
    assert opened.verification_level == VerificationLevel.GOVERNMENT_RECORD
    assert opened.confidence == 1.0


def test_normalize_blank_hcad_becomes_none_and_address_still_normalizes() -> None:
    rec = normalize_houston_code_record(_fixture_records()[ADDRESS_VIOLATION])

    assert rec.hcad_account_id is None  # blank string, not ""
    assert rec.raw_address == "1234 Westheimer Rd."
    assert rec.normalized_address == "1234 WESTHEIMER RD"


def test_normalize_truncates_ordinance_summary_to_500_chars() -> None:
    raw = _fixture_records()[ADDRESS_VIOLATION]
    assert raw["ShortDescription"] is not None and len(raw["ShortDescription"]) > 500

    rec = normalize_houston_code_record(raw)
    summary = rec.event_candidates[0].summary
    assert summary is not None
    assert len(summary) == 500
    assert raw["ShortDescription"].startswith(summary)


def test_normalize_never_fabricates_resolved_event_for_closed_status() -> None:
    rec = normalize_houston_code_record(_fixture_records()[CLOSED_VIOLATION])

    # The resource has no closure date; CLOSED status must not invent one.
    assert rec.raw_payload["Project_Status"] == "CLOSED"
    assert [c.event_type for c in rec.event_candidates] == [EventType.CODE_VIOLATION_OPENED]

    # And the same holds for every parseable record in the fixture.
    for raw in _fixture_record_list():
        try:
            normalized = normalize_houston_code_record(raw)
        except ValueError:
            continue
        assert [c.event_type for c in normalized.event_candidates] == [
            EventType.CODE_VIOLATION_OPENED
        ]


def test_normalize_title_falls_back_without_category() -> None:
    rec = normalize_houston_code_record(_fixture_records()[NO_CATEGORY_VIOLATION])

    opened = rec.event_candidates[0]
    assert opened.title == "Code violation opened"
    assert opened.summary is None


def test_normalize_never_puts_comment311upd_in_title_or_summary() -> None:
    saw_comment = False
    for raw in _fixture_record_list():
        try:
            rec = normalize_houston_code_record(raw)
        except ValueError:
            continue
        comment = raw.get("Comment311upd")
        for candidate in rec.event_candidates:
            text = candidate.title + " " + (candidate.summary or "")
            assert COMMENT_SENTINEL not in text
            if comment:
                saw_comment = True
                assert comment not in text
    assert saw_comment  # the fixture must keep exercising this


def test_normalize_rejects_malformed_record_create_date() -> None:
    with pytest.raises(ValueError, match="RecordCreateDate"):
        normalize_houston_code_record(_fixture_records()[MALFORMED_VIOLATION])


def test_normalize_requires_record_create_date() -> None:
    record = dict(_fixture_records()[OPEN_VIOLATION], RecordCreateDate=None)
    with pytest.raises(ValueError, match="RecordCreateDate"):
        normalize_houston_code_record(record)


def test_normalize_source_id_falls_back_to_npprjid_composite() -> None:
    raw = next(r for r in _fixture_record_list() if r["_id"] == COMPOSITE_ID_RECORD)
    assert raw["ViolationSubId"] is None

    rec = normalize_houston_code_record(raw)
    assert rec.source_record_id == f"{raw['NPPRJID']}-{COMPOSITE_ID_RECORD}"


def test_normalize_requires_some_record_identifier() -> None:
    record = dict(_fixture_records()[OPEN_VIOLATION], ViolationSubId=None, NPPRJID=None)
    with pytest.raises(ValueError, match="ViolationSubId"):
        normalize_houston_code_record(record)


def test_parse_ckan_datetime_accepts_dataset_and_iso_variants() -> None:
    expected = datetime(2016, 4, 29, tzinfo=UTC)
    for text in ("2016-04-29 00:00:00", "2016-04-29T00:00:00", "2016-04-29T00:00:00+00:00"):
        assert parse_ckan_datetime(text, field="RecordCreateDate") == expected
    assert parse_ckan_datetime(None, field="RecordCreateDate") is None
    assert parse_ckan_datetime("   ", field="RecordCreateDate") is None


# ---------------------------------------------------------------------------
# adapter: parse + fetch
# ---------------------------------------------------------------------------


def test_parse_yields_all_records_from_ckan_shaped_snapshot() -> None:
    adapter = HoustonCodeAdapter()
    snapshot = build_snapshot_from_file(FIXTURE_PATH)
    records = list(adapter.parse(snapshot))

    assert len(records) == 15
    assert all(isinstance(record, dict) for record in records)
    assert {record.get("ViolationSubId") for record in records} >= {
        CLOSED_VIOLATION,
        OPEN_VIOLATION,
        ADDRESS_VIOLATION,
        UNMATCHED_VIOLATION,
        MALFORMED_VIOLATION,
    }


def test_fetch_without_resource_id_tells_operator_to_use_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "houston_code_resource_id", "")
    with pytest.raises(ValueError, match="--file"):
        asyncio.run(HoustonCodeAdapter().fetch())


def test_fetch_paginates_ckan_and_writes_checksummed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "houston_code_resource_id", "res-test-123")
    all_records = [{"_id": i, "ViolationSubId": f"90{i}"} for i in range(1, 6)]
    offsets_seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"  # large filter sets must not ride the URL (414)
        assert request.url.path == "/api/3/action/datastore_search"
        params = json.loads(request.content)
        assert params["resource_id"] == "res-test-123"
        assert "filters" not in params  # none configured
        offset = int(params["offset"])
        limit = int(params["limit"])
        offsets_seen.append(offset)
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "records": all_records[offset : offset + limit],
                    "total": len(all_records),
                },
            },
        )

    adapter = HoustonCodeAdapter(
        raw_dir=tmp_path, page_size=2, transport=httpx.MockTransport(handler)
    )
    snapshot = asyncio.run(adapter.fetch())

    assert offsets_seen == [0, 2, 4]
    assert snapshot.source_name == SOURCE_NAME
    assert snapshot.storage_path.parent == tmp_path
    assert snapshot.source_url is not None and "res-test-123" in snapshot.source_url
    raw_bytes = snapshot.storage_path.read_bytes()
    assert snapshot.checksum == hashlib.sha256(raw_bytes).hexdigest()
    # The snapshot round-trips through parse with every page combined.
    assert list(adapter.parse(snapshot)) == all_records


def test_fetch_honors_max_records_and_zip_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "houston_code_resource_id", "res-test-123")
    all_records = [{"_id": i, "ViolationSubId": f"90{i}", "Zip": "77006"} for i in range(1, 6)]
    requests_seen: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"  # large filter sets must not ride the URL (414)
        params = json.loads(request.content)
        assert params["filters"] == {"Zip": "77006"}
        offset, limit = int(params["offset"]), int(params["limit"])
        requests_seen.append((offset, limit))
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "records": all_records[offset : offset + limit],
                    "total": len(all_records),
                },
            },
        )

    adapter = HoustonCodeAdapter(
        raw_dir=tmp_path,
        page_size=2,
        max_records=3,
        filters={"Zip": "77006"},
        transport=httpx.MockTransport(handler),
    )
    snapshot = asyncio.run(adapter.fetch())

    # Second page only asks for the single record still needed; paging stops at 3.
    assert requests_seen == [(0, 2), (2, 1)]
    assert list(adapter.parse(snapshot)) == all_records[:3]


def test_adapter_rejects_nonpositive_max_records() -> None:
    with pytest.raises(ValueError, match="max_records"):
        HoustonCodeAdapter(max_records=0)


# ---------------------------------------------------------------------------
# sync CLI: bounded live pulls
# ---------------------------------------------------------------------------


def test_cli_translates_zip_and_max_records_into_adapter_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "houston_code_resource_id", "res-live-1")
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
    exit_code = sync_mod.main(["--max-records", "25", "--zip", "77006"])

    assert exit_code == 0
    assert captured["snapshot"] is None  # live mode
    adapter = captured["adapter"]
    assert isinstance(adapter, HoustonCodeAdapter)
    assert adapter._max_records == 25
    assert adapter._filters == {"Zip": "77006"}


def test_cli_rejects_live_only_options_in_file_mode() -> None:
    with pytest.raises(SystemExit):
        sync_mod.main(["--file", str(FIXTURE_PATH), "--zip", "77006"])
    with pytest.raises(SystemExit):
        sync_mod.main(["--file", str(FIXTURE_PATH), "--max-records", "5"])


# ---------------------------------------------------------------------------
# end to end: fixture sync through the shared runner
# ---------------------------------------------------------------------------


def test_fixture_sync_end_to_end_and_idempotent(
    db: Session, run_session_factory: Callable[[], Session]
) -> None:
    prop = _make_property(db)
    db.commit()

    adapter = HoustonCodeAdapter()
    snapshot = build_snapshot_from_file(FIXTURE_PATH)
    run1 = run_sync(adapter, run_session_factory, snapshot=snapshot)

    # --- run metrics: malformed-date record rejected, run still SUCCEEDED ---
    assert run1.status == "SUCCEEDED"
    assert run1.error_message is None
    assert run1.records_parsed == 15
    assert run1.records_matched == 3
    assert run1.records_unmatched == 11
    assert run1.records_rejected == 1
    assert run1.source_records_created == 14
    assert run1.events_created == 3
    assert run1.parser_version == "2.0.0"
    assert run1.snapshot_path == str(FIXTURE_PATH.resolve())
    assert run1.snapshot_checksum == snapshot.checksum

    source_records = (
        db.execute(select(SourceRecord).where(SourceRecord.source_name == SOURCE_NAME))
        .scalars()
        .all()
    )
    by_sub_id = {sr.source_record_id: sr for sr in source_records}
    assert len(by_sub_id) == 14
    assert MALFORMED_VIOLATION not in by_sub_id  # rejected before any write
    assert "700790-8" in by_sub_id  # NPPRJID-_id composite fallback id
    assert all(sr.record_type == "code_enforcement_violation" for sr in source_records)

    # --- matched records: one OPENED event each, chronological, full provenance ---
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
        (e.event_type, e.event_date, e.title, _sub_id_of(by_sub_id, e)) for e in events
    ] == EXPECTED_TIMELINE
    for event in events:
        assert event.source_record_id is not None
        assert event.verification_level == VerificationLevel.GOVERNMENT_RECORD
        assert event.confidence == 1.0
        assert event.visibility == Visibility.PUBLIC

    # --- no fabricated RESOLVED/ACTION: the resource has no closure date ---
    non_opened = db.execute(
        select(func.count())
        .select_from(LedgerEvent)
        .where(
            LedgerEvent.source_record_id.in_([sr.id for sr in source_records]),
            LedgerEvent.event_type != EventType.CODE_VIOLATION_OPENED,
        )
    ).scalar_one()
    assert non_opened == 0
    # CLOSED status is retained in the raw payload only — the honest timeline.
    assert by_sub_id[CLOSED_VIOLATION].raw_payload["Project_Status"] == "CLOSED"

    # --- Comment311upd (may contain names) never surfaces in a public event ---
    all_source_events = (
        db.execute(
            select(LedgerEvent).where(
                LedgerEvent.source_record_id.in_([sr.id for sr in source_records])
            )
        )
        .scalars()
        .all()
    )
    fixture_comments = [
        record["Comment311upd"] for record in _fixture_record_list() if record["Comment311upd"]
    ]
    assert fixture_comments  # the fixture must keep planting comments
    for event in all_source_events:
        text = event.title + " " + (event.summary or "")
        assert COMMENT_SENTINEL not in text
        for comment in fixture_comments:
            assert comment not in text

    # --- ordinance summary made it through, truncated to 500 chars ---
    address_event = next(e for e in events if _sub_id_of(by_sub_id, e) == ADDRESS_VIOLATION)
    assert address_event.summary is not None and len(address_event.summary) == 500

    # --- HCAD rung for the two account-bearing records (confidence 1.0) ---
    for sub_id in (CLOSED_VIOLATION, OPEN_VIOLATION):
        match = db.execute(
            select(RecordPropertyMatch).where(
                RecordPropertyMatch.source_record_id == by_sub_id[sub_id].id
            )
        ).scalar_one()
        assert match.property_id == prop.id
        assert match.match_method == MatchMethod.HCAD_ID
        assert match.confidence == 1.0
        assert match.review_status == MatchReviewStatus.AUTO_ACCEPTED
        assert by_sub_id[sub_id].property_id == prop.id

    # --- blank-HCAD record falls through to the EXACT_ADDRESS rung ---
    address_match = db.execute(
        select(RecordPropertyMatch).where(
            RecordPropertyMatch.source_record_id == by_sub_id[ADDRESS_VIOLATION].id
        )
    ).scalar_one()
    assert address_match.property_id == prop.id
    assert address_match.match_method == MatchMethod.EXACT_ADDRESS
    assert address_match.confidence == 0.99
    assert address_match.review_status == MatchReviewStatus.AUTO_ACCEPTED

    # --- unmatched record: queue row with property_id None and ZERO events (§29) ---
    unmatched_sr = by_sub_id[UNMATCHED_VIOLATION]
    unmatched_match = db.execute(
        select(RecordPropertyMatch).where(
            RecordPropertyMatch.source_record_id == unmatched_sr.id
        )
    ).scalar_one()
    assert unmatched_match.property_id is None
    assert unmatched_match.match_method is None
    assert unmatched_match.review_status == MatchReviewStatus.UNMATCHED
    assert unmatched_match.match_reason
    assert unmatched_sr.property_id is None  # never silently attach
    assert (
        db.execute(
            select(func.count())
            .select_from(LedgerEvent)
            .where(LedgerEvent.source_record_id == unmatched_sr.id)
        ).scalar_one()
        == 0
    )

    # --- no events exist beyond the three expected ones for the matched property ---
    total_events = db.execute(
        select(func.count())
        .select_from(LedgerEvent)
        .where(LedgerEvent.source_record_id.in_([sr.id for sr in source_records]))
    ).scalar_one()
    assert total_events == 3

    # --- re-run with the same snapshot: zero new source_records/events ---
    run2 = run_sync(HoustonCodeAdapter(), run_session_factory, snapshot=snapshot)
    assert run2.status == "SUCCEEDED"
    assert run2.records_parsed == 15
    assert run2.records_matched == 3
    assert run2.records_unmatched == 11
    assert run2.records_rejected == 1
    assert run2.source_records_created == 0
    assert run2.events_created == 0

    assert (
        db.execute(
            select(func.count())
            .select_from(SourceRecord)
            .where(SourceRecord.source_name == SOURCE_NAME)
        ).scalar_one()
        == 14
    )
    assert (
        db.execute(
            select(func.count())
            .select_from(LedgerEvent)
            .where(LedgerEvent.property_id == prop.id)
        ).scalar_one()
        == 3
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
        assert run.records_parsed == 15
        assert run.records_rejected == 1
        assert run.parser_version == "2.0.0"
        assert run.snapshot_path == str(FIXTURE_PATH.resolve())
        assert run.snapshot_checksum == snapshot.checksum


def _sub_id_of(by_sub_id: dict[str, SourceRecord], event: LedgerEvent) -> str:
    """Resolve a ledger event back to the ViolationSubId of its source record."""
    for sub_id, source_record in by_sub_id.items():
        if source_record.id == event.source_record_id:
            return sub_id
    raise AssertionError(f"event {event.id} has no source record in this sync")
