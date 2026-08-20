"""Houston project-actions adapter tests: normalize mapping, NPPRJID gate + hook, sync.

The fixture (data/fixtures/houston_actions_sample/records.json) is shaped
exactly like a CKAN ``datastore_search`` response for the "Project Level
Actions Since 2014" resource — real field names (verified live against
data.houstontx.gov on 2026-08-19), synthetic values — and contains:

- three actions on linkable project NPPRJID 340418: a violation letter, a
  reinspection, and a "Close File" (RESOLVED),
- one action with a malformed Action_Date on the same project (rejected),
- one case-note row on the same project with an empty Action and a null
  Action_Id (composite id fallback, no event),
- six actions on unknown NPPRJIDs (dropped by the storage-economy gate),
- Comments planting the sentinel name "JANE EXAMPLESON", which must never
  surface in a public event title or summary.

Action records carry NO address and NO HCAD account: the only property
linkage is NPPRJID, resolved through previously ingested
``houston_code_enforcement`` violations via the runner's ``resolve_property``
hook.
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

import app.ingestion.houston_actions.sync as sync_mod
from app.core.config import settings
from app.enums import EventType, MatchMethod, MatchReviewStatus, VerificationLevel, Visibility
from app.ingestion.houston_actions.adapter import HoustonActionsAdapter
from app.ingestion.houston_actions.normalize import normalize_houston_action_record
from app.ingestion.houston_actions.sync import (
    build_snapshot_from_file,
    load_linked_npprjids,
    resolve_action_property,
)
from app.ingestion.runner import run_sync
from app.lib.address import address_hash, normalize_address
from app.models import LedgerEvent, Property, RecordPropertyMatch, SourceRecord, SourceSyncRun

SOURCE_NAME = "houston_code_actions"
VIOLATIONS_SOURCE_NAME = "houston_code_enforcement"
FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "houston_actions_sample"
    / "records.json"
)

LINKED_NPPRJID = "340418"  # the one project our synthetic violation was ingested for
COMMENT_SENTINEL = "JANE EXAMPLESON"  # fake name planted in Comments fields

LETTER_ACTION = "PA900001"  # Send Violation Letter -> CODE_VIOLATION_ACTION
REINSPECT_ACTION = "PA900002"  # Reinspection -> CODE_VIOLATION_ACTION (sentinel comment)
CLOSE_ACTION = "PA900003"  # Close File -> CODE_VIOLATION_RESOLVED
UNKNOWN_PROJECT_ACTION = "PA900004"  # NPPRJID 999999 -> dropped by the gate
MALFORMED_DATE_ACTION = "PA900005"  # Action_Date "13/45/2015" -> rejected
CASE_NOTE_COMPOSITE_ID = "340418-6"  # null Action_Id, empty Action -> no event

# (event_type, event_date, title, Action_Id) in strict chronological order.
EXPECTED_TIMELINE = [
    (
        EventType.CODE_VIOLATION_ACTION,
        datetime(2015, 2, 10, tzinfo=UTC),
        "Code enforcement action: Send Violation Letter",
        LETTER_ACTION,
    ),
    (
        EventType.CODE_VIOLATION_ACTION,
        datetime(2015, 4, 20, tzinfo=UTC),
        "Code enforcement action: Reinspection",
        REINSPECT_ACTION,
    ),
    (
        EventType.CODE_VIOLATION_RESOLVED,
        datetime(2015, 6, 30, tzinfo=UTC),
        "Code violation case closed",
        CLOSE_ACTION,
    ),
]


def _fixture_record_list() -> list[dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = payload["result"]["records"]
    return records


def _fixture_records() -> dict[str, dict[str, Any]]:
    """Fixture records keyed by Action_Id (records that carry one)."""
    return {
        record["Action_Id"]: record for record in _fixture_record_list() if record.get("Action_Id")
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
    normalized = normalize_address("5678 Sampson St")
    prop = Property(
        hcad_account_id="0450230000099",
        address_line1="5678 Sampson St",
        city="Houston",
        state="TX",
        postal_code="77004",
        normalized_address=normalized,
        address_hash=address_hash(normalized),
    )
    db.add(prop)
    db.flush()
    return prop


def _make_linked_violation(
    db: Session,
    prop: Property,
    *,
    npprjid: str = LINKED_NPPRJID,
    violation_sub_id: str = "910001",
    review_status: str = MatchReviewStatus.AUTO_ACCEPTED,
) -> SourceRecord:
    """An ingested houston_code_enforcement violation matched to ``prop``."""
    payload: dict[str, Any] = {
        "_id": 1,
        "NPPRJID": npprjid,
        "ViolationSubId": violation_sub_id,
        "HCAD": prop.hcad_account_id,
        "Merged_Situs": prop.address_line1,
        "Violation_Category": "Nuisance - High Weeds",
    }
    source_record = SourceRecord(
        source_name=VIOLATIONS_SOURCE_NAME,
        source_record_id=violation_sub_id,
        property_id=prop.id if review_status == MatchReviewStatus.AUTO_ACCEPTED else None,
        record_type="code_enforcement_violation",
        raw_payload=payload,
        content_hash=hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        parser_version="2.0.0",
    )
    db.add(source_record)
    db.flush()
    db.add(
        RecordPropertyMatch(
            source_record_id=source_record.id,
            property_id=prop.id,
            match_method=MatchMethod.HCAD_ID,
            confidence=1.0,
            review_status=review_status,
            match_reason="hcad account exact match",
        )
    )
    db.flush()
    return source_record


# ---------------------------------------------------------------------------
# normalize: field mapping (no database needed)
# ---------------------------------------------------------------------------


def test_normalize_close_file_becomes_resolved_event() -> None:
    rec = normalize_houston_action_record(_fixture_records()[CLOSE_ACTION])

    assert rec.record_type == "code_enforcement_action"
    assert rec.source_record_id == CLOSE_ACTION
    assert len(rec.event_candidates) == 1
    resolved = rec.event_candidates[0]
    assert resolved.event_type == EventType.CODE_VIOLATION_RESOLVED
    assert resolved.event_date == datetime(2015, 6, 30, tzinfo=UTC)
    assert resolved.title == "Code violation case closed"
    assert resolved.summary is None
    assert resolved.verification_level == VerificationLevel.GOVERNMENT_RECORD
    assert resolved.confidence == 1.0


def test_normalize_other_action_becomes_action_event() -> None:
    rec = normalize_houston_action_record(_fixture_records()[REINSPECT_ACTION])

    assert len(rec.event_candidates) == 1
    action = rec.event_candidates[0]
    assert action.event_type == EventType.CODE_VIOLATION_ACTION
    assert action.event_date == datetime(2015, 4, 20, tzinfo=UTC)
    assert action.title == "Code enforcement action: Reinspection"
    assert action.summary is None
    assert action.verification_level == VerificationLevel.GOVERNMENT_RECORD


def test_normalize_carries_no_address_and_no_hcad() -> None:
    # The resource has neither field; linkage happens only through NPPRJID.
    rec = normalize_houston_action_record(_fixture_records()[LETTER_ACTION])

    assert rec.normalized_address is None
    assert rec.raw_address is None
    assert rec.hcad_account_id is None
    assert rec.raw_payload["NPPRJID"] == LINKED_NPPRJID  # retained for the hook


def test_normalize_empty_action_yields_no_event_but_keeps_record() -> None:
    raw = next(r for r in _fixture_record_list() if r["_id"] == 6)
    assert raw["Action"] == "" and raw["Action_Id"] is None

    rec = normalize_houston_action_record(raw)
    assert rec.event_candidates == []  # a case note is not a public event
    assert rec.source_record_id == CASE_NOTE_COMPOSITE_ID  # NPPRJID-_id fallback
    assert rec.raw_payload == raw  # provenance retained


def test_normalize_rejects_malformed_action_date() -> None:
    with pytest.raises(ValueError, match="Action_Date"):
        normalize_houston_action_record(_fixture_records()[MALFORMED_DATE_ACTION])


def test_normalize_rejects_action_without_date() -> None:
    record = dict(_fixture_records()[CLOSE_ACTION], Action_Date=None)
    with pytest.raises(ValueError, match="Action_Date"):
        normalize_houston_action_record(record)


def test_normalize_requires_some_record_identifier() -> None:
    record = dict(_fixture_records()[CLOSE_ACTION], Action_Id=None, NPPRJID=None)
    with pytest.raises(ValueError, match="Action_Id"):
        normalize_houston_action_record(record)


def test_normalize_never_puts_comments_in_title_or_summary() -> None:
    saw_sentinel_comment = False
    for raw in _fixture_record_list():
        try:
            rec = normalize_houston_action_record(raw)
        except ValueError:
            continue
        comment = raw.get("Comments")
        if comment and COMMENT_SENTINEL in comment:
            saw_sentinel_comment = True
        for candidate in rec.event_candidates:
            text = candidate.title + " " + (candidate.summary or "")
            assert COMMENT_SENTINEL not in text
            if comment:
                assert comment not in text
    assert saw_sentinel_comment  # the fixture must keep exercising this


# ---------------------------------------------------------------------------
# adapter: parse (with and without the NPPRJID gate) + fetch
# ---------------------------------------------------------------------------


def test_parse_without_gate_yields_all_records() -> None:
    adapter = HoustonActionsAdapter()
    snapshot = build_snapshot_from_file(FIXTURE_PATH)
    records = list(adapter.parse(snapshot))

    assert len(records) == 11
    assert all(isinstance(record, dict) for record in records)


def test_parse_gate_drops_actions_on_unknown_projects() -> None:
    # Storage economy: with the linked-NPPRJID gate only actions on ingested
    # violation projects survive parse — the runner never sees the rest.
    adapter = HoustonActionsAdapter(linked_npprjids={LINKED_NPPRJID})
    snapshot = build_snapshot_from_file(FIXTURE_PATH)
    records = list(adapter.parse(snapshot))

    assert [record["_id"] for record in records] == [1, 2, 3, 5, 6]
    assert all(str(record["NPPRJID"]) == LINKED_NPPRJID for record in records)


def test_parse_gate_empty_set_drops_everything() -> None:
    adapter = HoustonActionsAdapter(linked_npprjids=set())
    snapshot = build_snapshot_from_file(FIXTURE_PATH)
    assert list(adapter.parse(snapshot)) == []


def test_fetch_without_resource_id_tells_operator_to_use_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "houston_actions_resource_id", "")
    with pytest.raises(ValueError, match="--file"):
        asyncio.run(HoustonActionsAdapter().fetch())


def test_fetch_paginates_ckan_and_writes_checksummed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "houston_actions_resource_id", "res-actions-1")
    all_records = [{"_id": i, "Action_Id": f"PA{i}"} for i in range(1, 6)]
    offsets_seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"  # large filter sets must not ride the URL (414)
        assert request.url.path == "/api/3/action/datastore_search"
        params = json.loads(request.content)
        assert params["resource_id"] == "res-actions-1"
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

    adapter = HoustonActionsAdapter(
        raw_dir=tmp_path, page_size=2, transport=httpx.MockTransport(handler)
    )
    snapshot = asyncio.run(adapter.fetch())

    assert offsets_seen == [0, 2, 4]
    assert snapshot.source_name == SOURCE_NAME
    assert snapshot.storage_path.parent == tmp_path
    assert snapshot.source_url is not None and "res-actions-1" in snapshot.source_url
    raw_bytes = snapshot.storage_path.read_bytes()
    assert snapshot.checksum == hashlib.sha256(raw_bytes).hexdigest()
    # The snapshot round-trips through parse with every page combined.
    assert list(adapter.parse(snapshot)) == all_records


def test_fetch_honors_max_records_and_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "houston_actions_resource_id", "res-actions-1")
    all_records = [{"_id": i, "Action_Id": f"PA{i}", "Action": "Close File"} for i in range(1, 6)]
    requests_seen: list[tuple[int, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        params = json.loads(request.content)
        assert params["filters"] == {"Action": "Close File"}
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

    adapter = HoustonActionsAdapter(
        raw_dir=tmp_path,
        page_size=2,
        max_records=3,
        filters={"Action": "Close File"},
        transport=httpx.MockTransport(handler),
    )
    snapshot = asyncio.run(adapter.fetch())

    # Second page only asks for the single record still needed; paging stops at 3.
    assert requests_seen == [(0, 2), (2, 1)]
    assert list(adapter.parse(snapshot)) == all_records[:3]


def test_fetch_uses_explicit_resource_id_for_pre2014_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "houston_actions_resource_id", "res-actions-1")

    def handler(request: httpx.Request) -> httpx.Response:
        params = json.loads(request.content)
        assert params["resource_id"] == "res-pre-2014"  # override wins over settings
        return httpx.Response(
            200,
            json={"success": True, "result": {"records": [{"_id": 1}], "total": 1}},
        )

    adapter = HoustonActionsAdapter(
        raw_dir=tmp_path, resource_id="res-pre-2014", transport=httpx.MockTransport(handler)
    )
    snapshot = asyncio.run(adapter.fetch())
    assert snapshot.source_url is not None and "res-pre-2014" in snapshot.source_url


def test_adapter_rejects_nonpositive_max_records() -> None:
    with pytest.raises(ValueError, match="max_records"):
        HoustonActionsAdapter(max_records=0)


# ---------------------------------------------------------------------------
# NPPRJID resolution: linked set + resolve_property hook (database)
# ---------------------------------------------------------------------------


def test_load_linked_npprjids_only_sees_violation_source(db: Session) -> None:
    prop = _make_property(db)
    _make_linked_violation(db, prop)
    # A record from an unrelated source with an NPPRJID must not leak in.
    db.add(
        SourceRecord(
            source_name="some_other_source",
            source_record_id="x-1",
            record_type="other",
            raw_payload={"NPPRJID": "999999"},
            content_hash="0" * 64,
        )
    )
    db.flush()

    assert load_linked_npprjids(db) == {LINKED_NPPRJID}


def test_resolve_action_property_links_through_violation_project(db: Session) -> None:
    prop = _make_property(db)
    _make_linked_violation(db, prop)
    rec = normalize_houston_action_record(_fixture_records()[CLOSE_ACTION])

    result = resolve_action_property(db, rec)

    assert result is not None
    assert result.property_id == prop.id
    assert result.method == MatchMethod.MANUAL
    assert result.confidence == 1.0
    assert result.review_status == MatchReviewStatus.AUTO_ACCEPTED
    assert f"NPPRJID {LINKED_NPPRJID}" in result.reason


def test_resolve_action_property_unknown_project_goes_to_queue(db: Session) -> None:
    rec = normalize_houston_action_record(_fixture_records()[UNKNOWN_PROJECT_ACTION])

    result = resolve_action_property(db, rec)

    assert result is not None
    assert result.property_id is None
    assert result.review_status == MatchReviewStatus.UNMATCHED
    assert "999999" in result.reason


def test_resolve_action_property_requires_npprjid(db: Session) -> None:
    record = dict(_fixture_records()[CLOSE_ACTION], NPPRJID=None)
    rec = normalize_houston_action_record(record)

    result = resolve_action_property(db, rec)

    assert result is not None
    assert result.property_id is None
    assert result.review_status == MatchReviewStatus.UNMATCHED


def test_resolve_action_property_ignores_review_required_violation_match(
    db: Session,
) -> None:
    # A violation whose own match is still under review must not transitively
    # attach actions — never silently attach an uncertain match.
    prop = _make_property(db)
    _make_linked_violation(db, prop, review_status=MatchReviewStatus.REVIEW_REQUIRED)
    rec = normalize_houston_action_record(_fixture_records()[CLOSE_ACTION])

    result = resolve_action_property(db, rec)

    assert result is not None
    assert result.property_id is None
    assert result.review_status == MatchReviewStatus.UNMATCHED


# ---------------------------------------------------------------------------
# sync CLI: mode validation + wiring of gate and hook
# ---------------------------------------------------------------------------


def test_cli_rejects_live_only_options_in_file_mode() -> None:
    with pytest.raises(SystemExit):
        sync_mod.main(["--file", str(FIXTURE_PATH), "--max-records", "5"])
    with pytest.raises(SystemExit):
        sync_mod.main(["--file", str(FIXTURE_PATH), "--pre-2014"])


def test_cli_wires_gate_hook_and_live_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "houston_actions_resource_id", "res-live-1")
    monkeypatch.setattr(sync_mod, "_known_project_ids", lambda: {LINKED_NPPRJID})
    captured: dict[str, Any] = {}

    def fake_run_sync(
        adapter: Any, session_factory: Any, *, snapshot: Any = None, resolve_property: Any = None
    ) -> Any:
        captured["adapter"] = adapter
        captured["snapshot"] = snapshot
        captured["resolve_property"] = resolve_property
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
    exit_code = sync_mod.main(["--max-records", "25"])

    assert exit_code == 0
    assert captured["snapshot"] is None  # live mode
    assert captured["resolve_property"] is sync_mod.resolve_action_property
    adapter = captured["adapter"]
    assert isinstance(adapter, HoustonActionsAdapter)
    assert adapter._max_records == 25
    assert adapter._linked_npprjids == {LINKED_NPPRJID}  # gate loaded before the run
    assert adapter._resource_id is None  # default resource unless --pre-2014


# ---------------------------------------------------------------------------
# end to end: fixture sync through the shared runner
# ---------------------------------------------------------------------------


def test_fixture_sync_end_to_end_and_idempotent(
    db: Session, run_session_factory: Callable[[], Session]
) -> None:
    prop = _make_property(db)
    _make_linked_violation(db, prop)
    db.commit()

    linked = load_linked_npprjids(db)
    assert linked == {LINKED_NPPRJID}

    snapshot = build_snapshot_from_file(FIXTURE_PATH)
    run1 = run_sync(
        HoustonActionsAdapter(linked_npprjids=linked),
        run_session_factory,
        snapshot=snapshot,
        resolve_property=resolve_action_property,
    )

    # --- gate + metrics: only the 5 linkable-project rows reached the runner ---
    assert run1.status == "SUCCEEDED"
    assert run1.error_message is None
    assert run1.records_parsed == 5
    assert run1.records_matched == 4  # 3 events + 1 case-note record
    assert run1.records_unmatched == 0
    assert run1.records_rejected == 1  # malformed Action_Date
    assert run1.source_records_created == 4
    assert run1.events_created == 3
    assert run1.parser_version == "1.0.0"
    assert run1.snapshot_path == str(FIXTURE_PATH.resolve())
    assert run1.snapshot_checksum == snapshot.checksum

    source_records = (
        db.execute(select(SourceRecord).where(SourceRecord.source_name == SOURCE_NAME))
        .scalars()
        .all()
    )
    by_action_id = {sr.source_record_id: sr for sr in source_records}
    assert set(by_action_id) == {
        LETTER_ACTION,
        REINSPECT_ACTION,
        CLOSE_ACTION,
        CASE_NOTE_COMPOSITE_ID,
    }
    assert MALFORMED_DATE_ACTION not in by_action_id  # rejected before any write
    # Unknown-project actions created NOTHING — no source records, no queue rows.
    assert UNKNOWN_PROJECT_ACTION not in by_action_id
    assert all(sr.record_type == "code_enforcement_action" for sr in source_records)
    assert all(sr.property_id == prop.id for sr in source_records)

    # --- events land on the violation's property, in date order, full provenance ---
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
        (e.event_type, e.event_date, e.title, _action_id_of(by_action_id, e)) for e in events
    ] == EXPECTED_TIMELINE
    for event in events:
        assert event.source_record_id is not None
        assert event.verification_level == VerificationLevel.GOVERNMENT_RECORD
        assert event.confidence == 1.0
        assert event.visibility == Visibility.PUBLIC

    # --- every match went through the NPPRJID hook: MANUAL at 1.0, reasoned ---
    for source_record in source_records:
        match = db.execute(
            select(RecordPropertyMatch).where(
                RecordPropertyMatch.source_record_id == source_record.id
            )
        ).scalar_one()
        assert match.property_id == prop.id
        assert match.match_method == MatchMethod.MANUAL
        assert match.confidence == 1.0
        assert match.review_status == MatchReviewStatus.AUTO_ACCEPTED
        assert f"NPPRJID {LINKED_NPPRJID}" in match.match_reason

    # --- the empty-Action case note produced a source record but ZERO events ---
    case_note = by_action_id[CASE_NOTE_COMPOSITE_ID]
    assert (
        db.execute(
            select(func.count())
            .select_from(LedgerEvent)
            .where(LedgerEvent.source_record_id == case_note.id)
        ).scalar_one()
        == 0
    )

    # --- Comments (may contain names) never surface in any public event ---
    fixture_comments = [
        record["Comments"] for record in _fixture_record_list() if record.get("Comments")
    ]
    assert any(COMMENT_SENTINEL in comment for comment in fixture_comments)
    all_events = db.execute(select(LedgerEvent)).scalars().all()
    for event in all_events:
        text = event.title + " " + (event.summary or "")
        assert COMMENT_SENTINEL not in text
        for comment in fixture_comments:
            assert comment not in text

    # --- no events beyond the three expected ones exist anywhere ---
    assert db.execute(select(func.count()).select_from(LedgerEvent)).scalar_one() == 3

    # --- re-run with the same snapshot: zero new rows anywhere ---
    run2 = run_sync(
        HoustonActionsAdapter(linked_npprjids=linked),
        run_session_factory,
        snapshot=snapshot,
        resolve_property=resolve_action_property,
    )
    assert run2.status == "SUCCEEDED"
    assert run2.records_parsed == 5
    assert run2.records_matched == 4
    assert run2.records_unmatched == 0
    assert run2.records_rejected == 1
    assert run2.source_records_created == 0
    assert run2.events_created == 0

    assert (
        db.execute(
            select(func.count())
            .select_from(SourceRecord)
            .where(SourceRecord.source_name == SOURCE_NAME)
        ).scalar_one()
        == 4
    )
    assert (
        db.execute(
            select(func.count()).select_from(LedgerEvent).where(LedgerEvent.property_id == prop.id)
        ).scalar_one()
        == 3
    )
    assert (
        db.execute(
            select(func.count())
            .select_from(RecordPropertyMatch)
            .where(RecordPropertyMatch.source_record_id.in_([sr.id for sr in source_records]))
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
        assert run.records_parsed == 5
        assert run.records_rejected == 1
        assert run.parser_version == "1.0.0"
        assert run.snapshot_path == str(FIXTURE_PATH.resolve())
        assert run.snapshot_checksum == snapshot.checksum


def test_sync_without_linked_violation_creates_no_events(
    db: Session, run_session_factory: Callable[[], Session]
) -> None:
    """No ingested violations at all: the gate filters every action out."""
    linked = load_linked_npprjids(db)
    assert linked == set()

    run = run_sync(
        HoustonActionsAdapter(linked_npprjids=linked),
        run_session_factory,
        snapshot=build_snapshot_from_file(FIXTURE_PATH),
        resolve_property=resolve_action_property,
    )

    assert run.status == "SUCCEEDED"
    assert run.records_parsed == 0
    assert run.source_records_created == 0
    assert run.events_created == 0
    assert (
        db.execute(
            select(func.count())
            .select_from(SourceRecord)
            .where(SourceRecord.source_name == SOURCE_NAME)
        ).scalar_one()
        == 0
    )


def _action_id_of(by_action_id: dict[str, SourceRecord], event: LedgerEvent) -> str:
    """Resolve a ledger event back to the Action_Id of its source record."""
    for action_id, source_record in by_action_id.items():
        if source_record.id == event.source_record_id:
            return action_id
    raise AssertionError(f"event {event.id} has no source record in this sync")
