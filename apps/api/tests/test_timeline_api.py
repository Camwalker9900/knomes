"""Timeline tests: ordering, provenance, filter groups, and visibility rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.enums import EventType
from app.lib.address import address_hash, normalize_address
from app.models import LedgerEvent, Property, SourceRecord
from app.services.timeline import get_timeline


def make_property(db: Session, line1: str) -> Property:
    normalized = normalize_address(line1)
    prop = Property(
        address_line1=line1,
        city="Houston",
        state="TX",
        postal_code="77000",
        normalized_address=normalized,
        address_hash=address_hash(normalized),
    )
    db.add(prop)
    db.flush()
    return prop


def make_source_record(
    db: Session,
    prop: Property,
    *,
    source_name: str,
    record_type: str,
    source_record_id: str,
    retrieved_at: datetime | None = None,
) -> SourceRecord:
    record = SourceRecord(
        source_name=source_name,
        source_record_id=source_record_id,
        property_id=prop.id,
        record_type=record_type,
        raw_payload={"id": source_record_id},
        content_hash=f"hash-{source_name}-{source_record_id}",
        retrieved_at=retrieved_at or datetime(2024, 1, 1, tzinfo=UTC),
    )
    db.add(record)
    db.flush()
    return record


def make_event(
    db: Session,
    prop: Property,
    event_type: str,
    event_date: datetime,
    *,
    title: str | None = None,
    summary: str | None = None,
    verification_level: str = "GOVERNMENT_RECORD",
    confidence: float | None = None,
    visibility: str = "PUBLIC",
    retracted_at: datetime | None = None,
    source_record: SourceRecord | None = None,
    created_at: datetime | None = None,
) -> LedgerEvent:
    event = LedgerEvent(
        property_id=prop.id,
        event_type=event_type,
        event_date=event_date,
        title=title or f"{event_type} event",
        summary=summary,
        verification_level=verification_level,
        confidence=confidence,
        visibility=visibility,
        retracted_at=retracted_at,
        source_record_id=source_record.id if source_record is not None else None,
    )
    if created_at is not None:
        event.created_at = created_at
    db.add(event)
    db.flush()
    return event


class TestTimelineOrderingAndProvenance:
    def test_events_chronological_with_provenance(
        self, client: TestClient, db: Session
    ) -> None:
        prop = make_property(db, "100 Test St")
        parcel = make_source_record(
            db, prop, source_name="hcad", record_type="parcel", source_record_id="P100"
        )
        permit = make_source_record(
            db,
            prop,
            source_name="city_of_houston_permits",
            record_type="permit",
            source_record_id="PMT-1",
        )
        # Inserted deliberately out of chronological order.
        make_event(
            db,
            prop,
            EventType.PERMIT_ISSUED,
            datetime(2020, 5, 1, tzinfo=UTC),
            source_record=permit,
            verification_level="GOVERNMENT_RECORD",
        )
        make_event(
            db,
            prop,
            EventType.INSPECTION_PERFORMED,
            datetime(2021, 3, 15, tzinfo=UTC),
            verification_level="LICENSED_PROFESSIONAL",
        )
        make_event(
            db,
            prop,
            EventType.PROPERTY_CREATED,
            datetime(2019, 1, 1, tzinfo=UTC),
            source_record=parcel,
            verification_level="GOVERNMENT_RECORD",
        )

        response = client.get(f"/api/v1/properties/{prop.id}/timeline")

        assert response.status_code == 200
        events = response.json()["events"]
        assert [e["event_type"] for e in events] == [
            "PROPERTY_CREATED",
            "PERMIT_ISSUED",
            "INSPECTION_PERFORMED",
        ]
        dates = [datetime.fromisoformat(e["event_date"]) for e in events]
        assert dates == sorted(dates)
        for event in events:
            assert event["verification_level"]

        created, issued, inspected = events
        assert created["verification_level"] == "GOVERNMENT_RECORD"
        assert created["provenance"] == {"source_name": "hcad", "record_type": "parcel"}
        assert issued["provenance"] == {
            "source_name": "city_of_houston_permits",
            "record_type": "permit",
        }
        assert inspected["verification_level"] == "LICENSED_PROFESSIONAL"
        assert inspected["provenance"] is None

    def test_same_date_events_ordered_by_created_at(self, db: Session) -> None:
        prop = make_property(db, "100 Test St")
        event_date = datetime(2022, 6, 1, tzinfo=UTC)
        second = make_event(
            db,
            prop,
            EventType.CODE_VIOLATION_ACTION,
            event_date,
            created_at=datetime(2024, 1, 2, 11, 0, tzinfo=UTC),
        )
        first = make_event(
            db,
            prop,
            EventType.CODE_VIOLATION_OPENED,
            event_date,
            created_at=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        )

        rows = get_timeline(db, prop.id)

        assert [event.id for event, _ in rows] == [first.id, second.id]


class TestTimelineFilters:
    def test_filter_permits_returns_only_permit_events(
        self, client: TestClient, db: Session
    ) -> None:
        prop = make_property(db, "100 Test St")
        base = datetime(2020, 1, 1, tzinfo=UTC)
        make_event(db, prop, EventType.PERMIT_APPLIED, base)
        make_event(db, prop, EventType.PERMIT_ISSUED, base + timedelta(days=10))
        make_event(db, prop, EventType.CODE_VIOLATION_OPENED, base + timedelta(days=20))
        make_event(db, prop, EventType.LISTING_ACTIVE, base + timedelta(days=30))
        make_event(db, prop, EventType.INSPECTION_PERFORMED, base + timedelta(days=40))

        response = client.get(
            f"/api/v1/properties/{prop.id}/timeline", params={"filter": "permits"}
        )

        assert response.status_code == 200
        events = response.json()["events"]
        assert [e["event_type"] for e in events] == ["PERMIT_APPLIED", "PERMIT_ISSUED"]

    def test_every_filter_group_matches_contract(self, db: Session) -> None:
        prop = make_property(db, "100 Test St")
        base = datetime(2015, 1, 1, tzinfo=UTC)
        for offset, event_type in enumerate(EventType):
            make_event(db, prop, event_type, base + timedelta(days=offset))

        expected: dict[str, set[str]] = {
            "transactions": {
                "LISTING_CREATED",
                "LISTING_PRICE_CHANGED",
                "LISTING_ACTIVE",
                "LISTING_UNDER_CONTRACT",
                "LISTING_PENDING",
                "LISTING_BACK_ON_MARKET",
                "LISTING_CLOSED",
                "LISTING_WITHDRAWN",
                "TRANSACTION_TERMINATED",
            },
            "inspections": {"INSPECTION_PERFORMED"},
            "findings": {
                "CONDITION_FINDING",
                "FINDING_DISPUTED",
                "FINDING_RESOLVED",
                "FINDING_REOPENED",
            },
            "repairs": {"REPAIR_RECOMMENDED", "REPAIR_REPORTED", "REPAIR_VERIFIED"},
            "permits": {
                "PERMIT_APPLIED",
                "PERMIT_ISSUED",
                "PERMIT_INSPECTION",
                "PERMIT_FINALIZED",
            },
            "code": {
                "CODE_VIOLATION_OPENED",
                "CODE_VIOLATION_ACTION",
                "CODE_VIOLATION_RESOLVED",
            },
            "ownership": {
                "PROPERTY_CREATED",
                "OWNERSHIP_TRANSFER",
                "DEED_RECORDED",
                "LIEN_RECORDED",
                "LIEN_RELEASED",
            },
        }
        for filter_name, expected_types in expected.items():
            got = {event.event_type for event, _ in get_timeline(db, prop.id, filter_name)}
            assert got == expected_types, f"filter {filter_name!r} mismatch"

        all_types = {event.event_type for event, _ in get_timeline(db, prop.id, "all")}
        assert all_types == {member.value for member in EventType}

    def test_unknown_filter_is_422(self, client: TestClient, db: Session) -> None:
        prop = make_property(db, "100 Test St")
        response = client.get(
            f"/api/v1/properties/{prop.id}/timeline", params={"filter": "bogus"}
        )
        assert response.status_code == 422


class TestTimelineVisibility:
    def test_private_pending_and_retracted_events_excluded(
        self, client: TestClient, db: Session
    ) -> None:
        prop = make_property(db, "100 Test St")
        base = datetime(2021, 1, 1, tzinfo=UTC)
        visible = make_event(db, prop, EventType.PERMIT_ISSUED, base)
        make_event(
            db, prop, EventType.INSPECTION_PERFORMED, base + timedelta(days=1),
            visibility="PRIVATE",
        )
        make_event(
            db, prop, EventType.CONDITION_FINDING, base + timedelta(days=2),
            visibility="PENDING_REVIEW",
        )
        make_event(
            db, prop, EventType.REPAIR_REPORTED, base + timedelta(days=3),
            retracted_at=datetime(2024, 5, 1, tzinfo=UTC),
        )

        response = client.get(f"/api/v1/properties/{prop.id}/timeline")

        assert response.status_code == 200
        events = response.json()["events"]
        assert [e["id"] for e in events] == [str(visible.id)]

    def test_timeline_unknown_property_is_404(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/properties/{uuid.uuid4()}/timeline")
        assert response.status_code == 404
