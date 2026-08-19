"""Houston code-enforcement adapter tests: normalize mapping, CKAN fetch, end-to-end sync.

The fixture (data/fixtures/houston_code_sample/records.json) is shaped exactly
like a CKAN datastore_search response and contains:

- two records normalizing to "1234 WESTHEIMER RD" (one OPEN, one CLOSED with a
  date_closed -> resolution event),
- one record whose address matches nothing (unmatched queue),
- one record with a malformed date_opened (rejected),
- eleven filler records at addresses that match nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.enums import EventType, MatchMethod, MatchReviewStatus, VerificationLevel, Visibility
from app.ingestion.houston_code.adapter import HoustonCodeAdapter
from app.ingestion.houston_code.normalize import normalize_houston_code_record
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

CLOSED_PROJECT = "COD-2024-018233"  # matched, CLOSED with date_closed
OPEN_PROJECT = "COD-2025-004411"  # matched, OPEN with a distinct last_action_date
UNMATCHED_PROJECT = "COD-2025-007702"  # address matches nothing
MALFORMED_PROJECT = "COD-2025-009915"  # malformed date_opened -> rejected

# (event_type, event_date, title, project_number) in strict chronological order.
EXPECTED_TIMELINE = [
    (
        EventType.CODE_VIOLATION_OPENED,
        datetime(2024, 11, 5, tzinfo=UTC),
        "Code violation opened: DANGEROUS BUILDING",
        CLOSED_PROJECT,
    ),
    (
        EventType.CODE_VIOLATION_RESOLVED,
        datetime(2025, 1, 20, tzinfo=UTC),
        "Code violation resolved",
        CLOSED_PROJECT,
    ),
    (
        EventType.CODE_VIOLATION_OPENED,
        datetime(2025, 3, 2, tzinfo=UTC),
        "Code violation opened: NUISANCE - JUNK MOTOR VEHICLE",
        OPEN_PROJECT,
    ),
    (
        EventType.CODE_VIOLATION_ACTION,
        datetime(2025, 3, 15, tzinfo=UTC),
        "Code enforcement action: NOTICE OF VIOLATION ISSUED",
        OPEN_PROJECT,
    ),
]


def _fixture_records() -> dict[str, dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = payload["result"]["records"]
    return {record["project_number"]: record for record in records}


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
        hcad_account_id="0660640130020",
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


def test_normalize_maps_open_project_with_distinct_action_date() -> None:
    rec = normalize_houston_code_record(_fixture_records()[OPEN_PROJECT])

    assert rec.record_type == "code_enforcement_project"
    assert rec.source_record_id == OPEN_PROJECT
    assert rec.raw_address == "1234 Westheimer Rd."
    assert rec.normalized_address == "1234 WESTHEIMER RD"
    assert rec.hcad_account_id is None
    assert rec.raw_payload["project_status"] == "OPEN"

    assert [c.event_type for c in rec.event_candidates] == [
        EventType.CODE_VIOLATION_OPENED,
        EventType.CODE_VIOLATION_ACTION,
    ]
    opened, action = rec.event_candidates
    assert opened.event_date == datetime(2025, 3, 2, tzinfo=UTC)
    assert opened.title == "Code violation opened: NUISANCE - JUNK MOTOR VEHICLE"
    assert opened.summary is not None and opened.summary.startswith("Two inoperable vehicles")
    assert action.event_date == datetime(2025, 3, 15, tzinfo=UTC)
    assert action.title == "Code enforcement action: NOTICE OF VIOLATION ISSUED"
    for candidate in rec.event_candidates:
        assert candidate.verification_level == VerificationLevel.GOVERNMENT_RECORD
        assert candidate.confidence == 1.0


def test_normalize_closed_project_emits_resolved_and_suppresses_same_day_action() -> None:
    rec = normalize_houston_code_record(_fixture_records()[CLOSED_PROJECT])

    # last_action_date == date_closed, so no separate ACTION event.
    assert [c.event_type for c in rec.event_candidates] == [
        EventType.CODE_VIOLATION_OPENED,
        EventType.CODE_VIOLATION_RESOLVED,
    ]
    opened, resolved = rec.event_candidates
    assert opened.event_date == datetime(2024, 11, 5, tzinfo=UTC)
    assert resolved.event_date == datetime(2025, 1, 20, tzinfo=UTC)
    assert resolved.title == "Code violation resolved"


def test_normalize_orders_all_three_events_chronologically() -> None:
    rec = normalize_houston_code_record(_fixture_records()["COD-2024-016120"])

    assert [c.event_type for c in rec.event_candidates] == [
        EventType.CODE_VIOLATION_OPENED,
        EventType.CODE_VIOLATION_ACTION,
        EventType.CODE_VIOLATION_RESOLVED,
    ]
    dates = [c.event_date for c in rec.event_candidates]
    assert dates == sorted(dates)


def test_normalize_rejects_malformed_date() -> None:
    with pytest.raises(ValueError, match="date_opened"):
        normalize_houston_code_record(_fixture_records()[MALFORMED_PROJECT])


def test_normalize_requires_date_opened() -> None:
    record = dict(_fixture_records()[OPEN_PROJECT], date_opened=None)
    with pytest.raises(ValueError, match="date_opened"):
        normalize_houston_code_record(record)


def test_normalize_falls_back_to_ckan_id_when_project_number_missing() -> None:
    record = dict(_fixture_records()[OPEN_PROJECT], project_number=None)
    rec = normalize_houston_code_record(record)
    assert rec.source_record_id == "2"


# ---------------------------------------------------------------------------
# adapter: parse + fetch
# ---------------------------------------------------------------------------


def test_parse_yields_all_records_from_ckan_shaped_snapshot() -> None:
    adapter = HoustonCodeAdapter()
    snapshot = build_snapshot_from_file(FIXTURE_PATH)
    records = list(adapter.parse(snapshot))

    assert len(records) == 15
    assert all(isinstance(record, dict) for record in records)
    assert {record["project_number"] for record in records} >= {
        CLOSED_PROJECT,
        OPEN_PROJECT,
        UNMATCHED_PROJECT,
        MALFORMED_PROJECT,
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
    all_records = [{"_id": i, "project_number": f"COD-PAGE-{i}"} for i in range(1, 6)]
    offsets_seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/3/action/datastore_search"
        params = dict(request.url.params)
        assert params["resource_id"] == "res-test-123"
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
    assert run1.records_matched == 2
    assert run1.records_unmatched == 12
    assert run1.records_rejected == 1
    assert run1.source_records_created == 14
    assert run1.events_created == 4
    assert run1.parser_version == "1.0.0"
    assert run1.snapshot_path == str(FIXTURE_PATH.resolve())
    assert run1.snapshot_checksum == snapshot.checksum

    source_records = (
        db.execute(select(SourceRecord).where(SourceRecord.source_name == SOURCE_NAME))
        .scalars()
        .all()
    )
    by_project = {sr.source_record_id: sr for sr in source_records}
    assert len(by_project) == 14
    assert MALFORMED_PROJECT not in by_project  # rejected before any write

    # --- matched records: events in chronological order with full provenance ---
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
        (e.event_type, e.event_date, e.title, _project_of(by_project, e)) for e in events
    ] == [
        (event_type, event_date, title, project)
        for event_type, event_date, title, project in EXPECTED_TIMELINE
    ]
    event_dates = [e.event_date for e in events]
    assert event_dates == sorted(event_dates)
    for event in events:
        assert event.source_record_id is not None
        assert event.verification_level == VerificationLevel.GOVERNMENT_RECORD
        assert event.confidence == 1.0
        assert event.visibility == Visibility.PUBLIC

    # --- the CLOSED project specifically yields OPENED and RESOLVED ---
    closed_events = [e for e in events if _project_of(by_project, e) == CLOSED_PROJECT]
    assert [e.event_type for e in closed_events] == [
        EventType.CODE_VIOLATION_OPENED,
        EventType.CODE_VIOLATION_RESOLVED,
    ]

    # --- both matched records attached via the address ladder (EXACT_ADDRESS) ---
    for project in (CLOSED_PROJECT, OPEN_PROJECT):
        match = db.execute(
            select(RecordPropertyMatch).where(
                RecordPropertyMatch.source_record_id == by_project[project].id
            )
        ).scalar_one()
        assert match.property_id == prop.id
        assert match.match_method == MatchMethod.EXACT_ADDRESS
        assert match.review_status == MatchReviewStatus.AUTO_ACCEPTED
        assert by_project[project].property_id == prop.id

    # --- unmatched record: queue row with property_id None and ZERO events (§29) ---
    unmatched_sr = by_project[UNMATCHED_PROJECT]
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

    # --- no events exist beyond the four expected ones for the matched property ---
    total_events = db.execute(
        select(func.count())
        .select_from(LedgerEvent)
        .where(LedgerEvent.source_record_id.in_([sr.id for sr in source_records]))
    ).scalar_one()
    assert total_events == 4

    # --- re-run with the same snapshot: zero new source_records/events ---
    run2 = run_sync(HoustonCodeAdapter(), run_session_factory, snapshot=snapshot)
    assert run2.status == "SUCCEEDED"
    assert run2.records_parsed == 15
    assert run2.records_matched == 2
    assert run2.records_unmatched == 12
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
        == 4
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
        assert run.parser_version == "1.0.0"
        assert run.snapshot_path == str(FIXTURE_PATH.resolve())
        assert run.snapshot_checksum == snapshot.checksum


def _project_of(by_project: dict[str, SourceRecord], event: LedgerEvent) -> str:
    """Resolve a ledger event back to the project_number of its source record."""
    for project, source_record in by_project.items():
        if source_record.id == event.source_record_id:
            return project
    raise AssertionError(f"event {event.id} has no source record in this sync")
