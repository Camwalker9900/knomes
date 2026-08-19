"""End-to-end sync runner tests: matching outcomes, ledger reconciliation, idempotency.

Fuzzy similarity values were verified against pg_trgm:
similarity('4501 RUNNER OAK DR', '4501 RUNNER OAKS DR') = 0.857 (>= 0.55)
similarity('4501 RUNNER OAK DR', '9800 ZANZIBAR CT')    = 0.000 (<  0.55)
similarity('700 IDEMPOTENT LN',  '9800 ZANZIBAR CT')    = 0.029 (<  0.55)
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session

from app.enums import MatchMethod, MatchReviewStatus, VerificationLevel, Visibility
from app.ingestion.base import EventCandidate, NormalizedRecord, RawSnapshot, SourceAdapter
from app.ingestion.runner import run_sync
from app.lib.address import address_hash, normalize_address
from app.models import (
    LedgerEvent,
    Property,
    RecordPropertyMatch,
    SourceRecord,
    SourceSyncRun,
)

SOURCE_NAME = "in_memory_test"

# One record per pipeline path: HCAD-id match, exact-address match, fuzzy
# (review only), unmatchable, and one that raises in normalize().
RECORDS: list[dict[str, Any]] = [
    {
        "id": "rec-hcad",
        "hcad": "TESTRUN100",
        "address": "4501 RUNNER OAK DR",
        "events": [
            {
                "event_type": "CODE_VIOLATION_OPENED",
                "event_date": "2026-01-05T00:00:00+00:00",
                "title": "Code violation opened",
            },
            {
                "event_type": "CODE_VIOLATION_RESOLVED",
                "event_date": "2026-02-10T00:00:00+00:00",
                "title": "Code violation resolved",
            },
        ],
    },
    {
        "id": "rec-exact",
        "address": "700 IDEMPOTENT LN",
        "events": [
            {
                "event_type": "PERMIT_ISSUED",
                "event_date": "2026-03-01T00:00:00+00:00",
                "title": "Permit issued",
            },
        ],
    },
    {
        "id": "rec-fuzzy",
        "address": "4501 RUNNER OAKS DR",
        "events": [
            {
                "event_type": "PERMIT_ISSUED",
                "event_date": "2026-04-01T00:00:00+00:00",
                "title": "Permit issued (must never reach the ledger)",
            },
        ],
    },
    {
        "id": "rec-unmatched",
        "address": "9800 ZANZIBAR CT",
        "events": [
            {
                "event_type": "PERMIT_APPLIED",
                "event_date": "2026-05-01T00:00:00+00:00",
                "title": "Permit applied (must never reach the ledger)",
            },
        ],
    },
    {
        "id": "rec-broken",
        "malformed": True,
    },
]


class InMemoryAdapter(SourceAdapter):
    """Adapter over an in-memory record list, for exercising the runner."""

    source_name = SOURCE_NAME
    parser_version = "test-0.1"

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    async def fetch(self) -> RawSnapshot:
        return RawSnapshot(
            source_name=self.source_name,
            storage_path=Path("/dev/null"),
            checksum="fetched-checksum",
            retrieved_at=datetime(2026, 8, 1, tzinfo=UTC),
            source_url=None,
        )

    def parse(self, snapshot: RawSnapshot) -> Iterator[dict[str, Any]]:
        yield from self._records

    def normalize(self, record: dict[str, Any]) -> NormalizedRecord:
        if record.get("malformed"):
            raise ValueError(f"malformed record {record['id']}")
        candidates = [
            EventCandidate(
                event_type=event["event_type"],
                event_date=datetime.fromisoformat(event["event_date"]),
                title=event["title"],
                summary=None,
                verification_level=VerificationLevel.GOVERNMENT_RECORD,
                confidence=1.0,
            )
            for event in record.get("events", [])
        ]
        return NormalizedRecord(
            record_type="test_record",
            source_record_id=record["id"],
            raw_payload=record,
            normalized_address=record.get("address"),
            hcad_account_id=record.get("hcad"),
            event_candidates=candidates,
            raw_address=record.get("address"),
        )


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


def _make_property(
    db: Session, *, address_line1: str, hcad_account_id: str | None = None
) -> Property:
    normalized = normalize_address(address_line1)
    prop = Property(
        hcad_account_id=hcad_account_id,
        address_line1=address_line1,
        city="Houston",
        state="TX",
        postal_code="77000",
        normalized_address=normalized,
        address_hash=address_hash(normalized),
    )
    db.add(prop)
    db.flush()
    return prop


def _snapshot() -> RawSnapshot:
    return RawSnapshot(
        source_name=SOURCE_NAME,
        storage_path=Path("data/raw/in_memory_test.json"),
        checksum="c" * 64,
        retrieved_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
        source_url="https://example.test/in-memory",
    )


def _count(db: Session, stmt: Any) -> int:
    return int(db.execute(stmt).scalar_one())


def test_run_sync_is_idempotent_end_to_end(
    db: Session, run_session_factory: Callable[[], Session]
) -> None:
    prop_a = _make_property(
        db, address_line1="4501 Runner Oak Drive", hcad_account_id="TESTRUN100"
    )
    prop_b = _make_property(db, address_line1="700 Idempotent Lane")
    db.commit()

    snapshot = _snapshot()
    run1 = run_sync(InMemoryAdapter(RECORDS), run_session_factory, snapshot=snapshot)

    # --- first run metrics ---
    assert run1.status == "SUCCEEDED"
    assert run1.error_message is None
    assert run1.finished_at is not None
    assert run1.records_parsed == 5
    assert run1.records_matched == 3  # hcad + exact + fuzzy candidate
    assert run1.records_unmatched == 1
    assert run1.records_rejected == 1
    assert run1.source_records_created == 4
    assert run1.events_created == 3  # 2 for rec-hcad + 1 for rec-exact
    assert run1.snapshot_path == str(snapshot.storage_path)
    assert run1.snapshot_checksum == snapshot.checksum
    assert run1.source_url == snapshot.source_url
    assert run1.parser_version == "test-0.1"

    source_records = (
        db.execute(select(SourceRecord).where(SourceRecord.source_name == SOURCE_NAME))
        .scalars()
        .all()
    )
    assert len(source_records) == 4
    by_record_id = {sr.source_record_id: sr for sr in source_records}
    assert set(by_record_id) == {"rec-hcad", "rec-exact", "rec-fuzzy", "rec-unmatched"}
    source_record_ids = [sr.id for sr in source_records]

    def match_rows(sr: SourceRecord) -> list[RecordPropertyMatch]:
        return list(
            db.execute(
                select(RecordPropertyMatch).where(
                    RecordPropertyMatch.source_record_id == sr.id
                )
            )
            .scalars()
            .all()
        )

    def event_count(sr: SourceRecord) -> int:
        return _count(
            db,
            select(func.count())
            .select_from(LedgerEvent)
            .where(LedgerEvent.source_record_id == sr.id),
        )

    # --- auto-accepted records produced PUBLIC events with provenance ---
    hcad_events = (
        db.execute(
            select(LedgerEvent).where(LedgerEvent.source_record_id == by_record_id["rec-hcad"].id)
        )
        .scalars()
        .all()
    )
    assert len(hcad_events) == 2
    assert all(event.property_id == prop_a.id for event in hcad_events)
    assert all(event.visibility == Visibility.PUBLIC for event in hcad_events)
    assert all(
        event.verification_level == VerificationLevel.GOVERNMENT_RECORD for event in hcad_events
    )
    (hcad_match,) = match_rows(by_record_id["rec-hcad"])
    assert hcad_match.review_status == MatchReviewStatus.AUTO_ACCEPTED
    assert hcad_match.match_method == MatchMethod.HCAD_ID
    assert hcad_match.property_id == prop_a.id
    assert by_record_id["rec-hcad"].property_id == prop_a.id

    exact_events = (
        db.execute(
            select(LedgerEvent).where(
                LedgerEvent.source_record_id == by_record_id["rec-exact"].id
            )
        )
        .scalars()
        .all()
    )
    assert len(exact_events) == 1
    assert exact_events[0].property_id == prop_b.id
    (exact_match,) = match_rows(by_record_id["rec-exact"])
    assert exact_match.match_method == MatchMethod.EXACT_ADDRESS
    assert exact_match.review_status == MatchReviewStatus.AUTO_ACCEPTED

    # --- fuzzy record: REVIEW_REQUIRED match row, candidate attached, NO events ---
    (fuzzy_match,) = match_rows(by_record_id["rec-fuzzy"])
    assert fuzzy_match.review_status == MatchReviewStatus.REVIEW_REQUIRED
    assert fuzzy_match.match_method == MatchMethod.FUZZY_ADDRESS
    assert fuzzy_match.property_id == prop_a.id
    assert 0.55 <= fuzzy_match.confidence < 0.99
    assert "similarity" in fuzzy_match.match_reason
    assert event_count(by_record_id["rec-fuzzy"]) == 0
    assert by_record_id["rec-fuzzy"].property_id is None  # never silently attach

    # --- unmatched record: queue row with property_id None, NO events ---
    (unmatched_match,) = match_rows(by_record_id["rec-unmatched"])
    assert unmatched_match.review_status == MatchReviewStatus.UNMATCHED
    assert unmatched_match.property_id is None
    assert unmatched_match.match_method is None
    assert unmatched_match.match_reason
    assert event_count(by_record_id["rec-unmatched"]) == 0
    assert by_record_id["rec-unmatched"].property_id is None

    total_events_stmt = (
        select(func.count())
        .select_from(LedgerEvent)
        .where(LedgerEvent.source_record_id.in_(source_record_ids))
    )
    total_matches_stmt = (
        select(func.count())
        .select_from(RecordPropertyMatch)
        .where(RecordPropertyMatch.source_record_id.in_(source_record_ids))
    )
    total_source_records_stmt = (
        select(func.count())
        .select_from(SourceRecord)
        .where(SourceRecord.source_name == SOURCE_NAME)
    )
    assert _count(db, total_events_stmt) == 3
    assert _count(db, total_matches_stmt) == 4

    # --- second run with the same snapshot: nothing new is created ---
    run2 = run_sync(InMemoryAdapter(RECORDS), run_session_factory, snapshot=snapshot)

    assert run2.status == "SUCCEEDED"
    assert run2.records_parsed == 5
    assert run2.records_matched == 3
    assert run2.records_unmatched == 1
    assert run2.records_rejected == 1
    assert run2.source_records_created == 0  # counters prove idempotency
    assert run2.events_created == 0

    assert _count(db, total_source_records_stmt) == 4
    assert _count(db, total_events_stmt) == 3
    assert _count(db, total_matches_stmt) == 4

    # --- both sync run rows persisted with all metrics populated ---
    runs = (
        db.execute(select(SourceSyncRun).where(SourceSyncRun.source_name == SOURCE_NAME))
        .scalars()
        .all()
    )
    assert {run.id for run in runs} == {run1.id, run2.id}
    for run in runs:
        assert run.status == "SUCCEEDED"
        assert run.error_message is None
        assert run.started_at is not None
        assert run.finished_at is not None
        assert run.records_parsed == 5
        assert run.records_rejected == 1
        assert run.snapshot_path == str(snapshot.storage_path)
        assert run.snapshot_checksum == snapshot.checksum
        assert run.parser_version == "test-0.1"


def test_run_sync_fetches_snapshot_when_not_provided(
    db: Session, run_session_factory: Callable[[], Session]
) -> None:
    run = run_sync(InMemoryAdapter([]), run_session_factory)

    assert run.status == "SUCCEEDED"
    assert run.records_parsed == 0
    assert run.snapshot_path == "/dev/null"
    assert run.snapshot_checksum == "fetched-checksum"
    assert run.finished_at is not None


# ---------------------------------------------------------------------------
# regressions: refreshed payloads, invalid candidates, failed-run metrics
# ---------------------------------------------------------------------------


def test_refreshed_payload_does_not_duplicate_events(
    db: Session, run_session_factory: Callable[[], Session]
) -> None:
    """A mutated upstream record versions source_records but never re-emits
    the same logical public event (dedup is on logical record identity)."""
    _make_property(db, address_line1="4501 RUNNER OAK DR", hcad_account_id="TESTRUN100")
    opened = {
        "event_type": "CODE_VIOLATION_OPENED",
        "event_date": "2026-01-05T00:00:00+00:00",
        "title": "Code violation opened",
    }
    rec_v1 = {
        "id": "rec-refresh",
        "hcad": "TESTRUN100",
        "address": "4501 RUNNER OAK DR",
        "status": "OPEN",
        "events": [opened],
    }
    run1 = run_sync(InMemoryAdapter([rec_v1]), run_session_factory, snapshot=_snapshot())
    assert run1.events_created == 1

    # Upstream record mutates in place (CKAN-style): same logical id, new
    # content hash, one genuinely new event candidate.
    rec_v2 = {
        **rec_v1,
        "status": "IN PROGRESS",
        "events": [
            opened,
            {
                "event_type": "CODE_VIOLATION_ACTION",
                "event_date": "2026-03-01T00:00:00+00:00",
                "title": "Code enforcement action: NOTICE ISSUED",
            },
        ],
    }
    run2 = run_sync(InMemoryAdapter([rec_v2]), run_session_factory, snapshot=_snapshot())

    versions = _count(
        db,
        select(func.count())
        .select_from(SourceRecord)
        .where(SourceRecord.source_record_id == "rec-refresh"),
    )
    assert versions == 2  # raw retention per spec section 7

    assert run2.events_created == 1  # only the new ACTION event
    opened_events = _count(
        db,
        select(func.count())
        .select_from(LedgerEvent)
        .where(LedgerEvent.event_type == "CODE_VIOLATION_OPENED"),
    )
    assert opened_events == 1  # no duplicate on the public timeline


def test_invalid_event_candidate_enum_counts_rejected(
    db: Session, run_session_factory: Callable[[], Session]
) -> None:
    _make_property(db, address_line1="4501 RUNNER OAK DR", hcad_account_id="TESTRUN100")
    rec = {
        "id": "rec-bad-enum",
        "hcad": "TESTRUN100",
        "address": "4501 RUNNER OAK DR",
        "events": [
            {
                "event_type": "NOT_A_REAL_EVENT_TYPE",
                "event_date": "2026-01-05T00:00:00+00:00",
                "title": "Bogus",
            }
        ],
    }
    run = run_sync(InMemoryAdapter([rec]), run_session_factory, snapshot=_snapshot())
    assert run.status == "SUCCEEDED"
    assert run.records_rejected == 1
    assert run.events_created == 0


class _ExplodingAdapter(InMemoryAdapter):
    """Yields one good record, then dies mid-parse (e.g. truncated snapshot)."""

    def parse(self, snapshot: RawSnapshot) -> Iterator[dict[str, Any]]:
        yield from super().parse(snapshot)
        raise RuntimeError("snapshot truncated mid-stream")


def test_failed_run_reports_zero_created_rows(
    db: Session, run_session_factory: Callable[[], Session]
) -> None:
    _make_property(db, address_line1="4501 RUNNER OAK DR", hcad_account_id="TESTRUN100")
    rec = {
        "id": "rec-pre-crash",
        "hcad": "TESTRUN100",
        "address": "4501 RUNNER OAK DR",
        "events": [
            {
                "event_type": "PERMIT_ISSUED",
                "event_date": "2026-04-01T00:00:00+00:00",
                "title": "Mechanical permit issued",
            }
        ],
    }
    run = run_sync(_ExplodingAdapter([rec]), run_session_factory, snapshot=_snapshot())
    assert run.status == "FAILED"
    # The write transaction rolled back, so created counters must not claim rows.
    assert run.source_records_created == 0
    assert run.events_created == 0
    remaining = _count(
        db,
        select(func.count())
        .select_from(SourceRecord)
        .where(SourceRecord.source_record_id == "rec-pre-crash"),
    )
    assert remaining == 0
