"""Property detail, findings, transactions, and sources endpoint tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.enums import EventType
from app.lib.address import address_hash, normalize_address
from app.models import (
    Finding,
    FindingResolution,
    LedgerEvent,
    Property,
    SourceRecord,
    SourceSyncRun,
    TransactionCycle,
)


def make_property(
    db: Session,
    line1: str,
    *,
    hcad: str | None = None,
    unit: str | None = None,
    year_built: int | None = None,
    building_sqft: int | None = None,
    lot_sqft: float | None = None,
    property_type: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> Property:
    normalized = normalize_address(line1)
    prop = Property(
        address_line1=line1,
        unit=unit,
        city="Houston",
        state="TX",
        postal_code="77000",
        normalized_address=normalized,
        address_hash=address_hash(normalized),
        hcad_account_id=hcad,
        year_built=year_built,
        building_sqft=building_sqft,
        lot_sqft=lot_sqft,
        property_type=property_type,
        latitude=latitude,
        longitude=longitude,
    )
    db.add(prop)
    db.flush()
    return prop


def make_event(
    db: Session,
    prop: Property,
    event_type: str,
    event_date: datetime,
    *,
    verification_level: str = "GOVERNMENT_RECORD",
    visibility: str = "PUBLIC",
    retracted_at: datetime | None = None,
) -> LedgerEvent:
    event = LedgerEvent(
        property_id=prop.id,
        event_type=event_type,
        event_date=event_date,
        title=f"{event_type} event",
        verification_level=verification_level,
        visibility=visibility,
        retracted_at=retracted_at,
    )
    db.add(event)
    db.flush()
    return event


def make_finding(
    db: Session,
    prop: Property,
    *,
    status: str,
    category: str = "HVAC",
    title: str = "Condensate leak at air handler",
    severity: str | None = "MODERATE",
    first_observed_at: datetime | None = None,
    latest_observed_at: datetime | None = None,
) -> Finding:
    finding = Finding(
        property_id=prop.id,
        category=category,
        title=title,
        severity=severity,
        status=status,
        first_observed_at=first_observed_at,
        latest_observed_at=latest_observed_at,
    )
    db.add(finding)
    db.flush()
    return finding


def make_source_record(
    db: Session,
    prop: Property,
    *,
    source_name: str,
    source_record_id: str,
    record_type: str = "record",
    retrieved_at: datetime,
) -> SourceRecord:
    record = SourceRecord(
        source_name=source_name,
        source_record_id=source_record_id,
        property_id=prop.id,
        record_type=record_type,
        raw_payload={"id": source_record_id},
        content_hash=f"hash-{source_name}-{source_record_id}",
        retrieved_at=retrieved_at,
    )
    db.add(record)
    db.flush()
    return record


class TestPropertyDetail:
    def test_unknown_property_is_404_everywhere(self, client: TestClient) -> None:
        missing = uuid.uuid4()
        for suffix in ("", "/timeline", "/findings", "/transactions", "/sources"):
            response = client.get(f"/api/v1/properties/{missing}{suffix}")
            assert response.status_code == 404, f"expected 404 for {suffix or 'detail'}"

    def test_invalid_uuid_is_422(self, client: TestClient) -> None:
        response = client.get("/api/v1/properties/not-a-uuid")
        assert response.status_code == 422

    def test_detail_fields_and_condition_summary_counts(
        self, client: TestClient, db: Session
    ) -> None:
        prop = make_property(
            db,
            "100 Test St",
            hcad="TEST000100",
            year_built=1985,
            building_sqft=2200,
            lot_sqft=7500.0,
            property_type="Single Family",
            latitude=29.76,
            longitude=-95.36,
        )
        # Findings: 3 open-ish, 1 resolved, 1 disputed; SUPERSEDED/UNKNOWN never counted.
        for status in (
            "OPEN",
            "RESOLUTION_REPORTED",
            "RESOLUTION_EVIDENCE_FOUND",
            "RESOLVED",
            "DISPUTED",
            "SUPERSEDED",
            "UNKNOWN",
        ):
            make_finding(db, prop, status=status)
        # Inspections: two count as verified; participant-reported, private, retracted do not.
        base = datetime(2021, 4, 1, tzinfo=UTC)
        make_event(
            db, prop, EventType.INSPECTION_PERFORMED, base,
            verification_level="GOVERNMENT_RECORD",
        )
        make_event(
            db, prop, EventType.INSPECTION_PERFORMED, base.replace(day=2),
            verification_level="LICENSED_PROFESSIONAL",
        )
        make_event(
            db, prop, EventType.INSPECTION_PERFORMED, base.replace(day=3),
            verification_level="PARTICIPANT_REPORTED",
        )
        make_event(
            db, prop, EventType.INSPECTION_PERFORMED, base.replace(day=4),
            verification_level="DOCUMENT_SUPPORTED", visibility="PRIVATE",
        )
        make_event(
            db, prop, EventType.INSPECTION_PERFORMED, base.replace(day=5),
            verification_level="TRANSACTION_DOCUMENT",
            retracted_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        db.add_all(
            [
                TransactionCycle(property_id=prop.id, outcome="TERMINATED"),
                TransactionCycle(property_id=prop.id, outcome="CLOSED"),
            ]
        )
        # A different property's data must not leak into the counts.
        other = make_property(db, "900 Other St")
        make_finding(db, other, status="OPEN")
        db.add(TransactionCycle(property_id=other.id))
        db.flush()

        response = client.get(f"/api/v1/properties/{prop.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(prop.id)
        assert body["address_line1"] == "100 Test St"
        assert body["unit"] is None
        assert body["city"] == "Houston"
        assert body["state"] == "TX"
        assert body["postal_code"] == "77000"
        assert body["hcad_account_id"] == "TEST000100"
        assert body["year_built"] == 1985
        assert body["building_sqft"] == 2200
        assert body["lot_sqft"] == 7500.0
        assert body["property_type"] == "Single Family"
        assert body["latitude"] == 29.76
        assert body["longitude"] == -95.36
        assert body["condition_summary"] == {
            "open_findings": 3,
            "resolved_findings": 1,
            "disputed_findings": 1,
            "verified_inspections": 2,
            "prior_transactions": 2,
        }
        assert body["freshness"] == []

    def test_detail_freshness_per_source(self, client: TestClient, db: Session) -> None:
        prop = make_property(db, "100 Test St")
        make_source_record(
            db, prop, source_name="hcad", source_record_id="A1",
            retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        make_source_record(
            db, prop, source_name="hcad", source_record_id="A2",
            retrieved_at=datetime(2024, 2, 1, tzinfo=UTC),
        )
        make_source_record(
            db, prop, source_name="houston_code", source_record_id="C1",
            retrieved_at=datetime(2024, 3, 5, tzinfo=UTC),
        )
        # A finished sync run later than any record retrieval wins for that source.
        db.add(
            SourceSyncRun(
                source_name="hcad",
                status="SUCCEEDED",
                finished_at=datetime(2024, 4, 1, tzinfo=UTC),
            )
        )
        # A still-running sync (no finished_at) is ignored.
        db.add(SourceSyncRun(source_name="houston_code", status="RUNNING"))
        db.flush()

        response = client.get(f"/api/v1/properties/{prop.id}")

        assert response.status_code == 200
        freshness = response.json()["freshness"]
        assert [entry["source"] for entry in freshness] == ["hcad", "houston_code"]
        parsed = {
            entry["source"]: datetime.fromisoformat(entry["last_refreshed"])
            for entry in freshness
        }
        assert parsed["hcad"] == datetime(2024, 4, 1, tzinfo=UTC)
        assert parsed["houston_code"] == datetime(2024, 3, 5, tzinfo=UTC)


class TestFindingsEndpoint:
    def test_findings_with_resolutions_ordered_by_resolved_at(
        self, client: TestClient, db: Session
    ) -> None:
        prop = make_property(db, "100 Test St")
        finding = make_finding(
            db,
            prop,
            status="RESOLVED",
            category="HVAC",
            first_observed_at=datetime(2023, 5, 1, tzinfo=UTC),
            latest_observed_at=datetime(2023, 6, 1, tzinfo=UTC),
        )
        bare_finding = make_finding(
            db, prop, status="OPEN", category="ROOF", title="Hail damage on south slope",
            first_observed_at=datetime(2023, 7, 1, tzinfo=UTC),
        )
        permit_event = make_event(
            db, prop, EventType.PERMIT_FINALIZED, datetime(2024, 3, 1, tzinfo=UTC)
        )
        later = FindingResolution(
            finding_id=finding.id,
            resolution_type="REPAIR",
            description="Coil replaced by licensed contractor",
            verification_level="LICENSED_PROFESSIONAL",
            resolved_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        earlier = FindingResolution(
            finding_id=finding.id,
            resolution_type="PERMIT_FINALIZATION",
            verification_level="GOVERNMENT_RECORD",
            resolved_at=datetime(2024, 3, 1, tzinfo=UTC),
            event_id=permit_event.id,
        )
        undated = FindingResolution(
            finding_id=finding.id,
            resolution_type="SELLER_DOCUMENTATION",
            verification_level="PARTICIPANT_REPORTED",
            resolved_at=None,
        )
        db.add_all([later, earlier, undated])
        db.flush()

        response = client.get(f"/api/v1/properties/{prop.id}/findings")

        assert response.status_code == 200
        findings = response.json()["findings"]
        assert [f["id"] for f in findings] == [str(finding.id), str(bare_finding.id)]

        detailed = findings[0]
        assert detailed["category"] == "HVAC"
        assert detailed["status"] == "RESOLVED"
        assert datetime.fromisoformat(detailed["first_observed_at"]) == datetime(
            2023, 5, 1, tzinfo=UTC
        )
        resolutions = detailed["resolutions"]
        assert [r["resolution_type"] for r in resolutions] == [
            "PERMIT_FINALIZATION",
            "REPAIR",
            "SELLER_DOCUMENTATION",
        ]
        assert resolutions[0]["id"] == str(earlier.id)
        assert resolutions[0]["event_id"] == str(permit_event.id)
        assert resolutions[0]["verification_level"] == "GOVERNMENT_RECORD"
        assert datetime.fromisoformat(resolutions[0]["resolved_at"]) == datetime(
            2024, 3, 1, tzinfo=UTC
        )
        assert resolutions[1]["event_id"] is None
        assert resolutions[2]["resolved_at"] is None

        assert findings[1]["resolutions"] == []


class TestTransactionsEndpoint:
    def test_transactions_include_reason_verification_level(
        self, client: TestClient, db: Session
    ) -> None:
        prop = make_property(db, "200 Test St")
        terminated = TransactionCycle(
            property_id=prop.id,
            listing_id="MLS-1",
            started_at=datetime(2023, 1, 1, tzinfo=UTC),
            under_contract_at=datetime(2023, 2, 1, tzinfo=UTC),
            terminated_at=datetime(2023, 3, 1, tzinfo=UTC),
            outcome="TERMINATED",
            termination_reason="UNKNOWN",
            reason_verification_level="SYSTEM_INFERRED",
        )
        closed = TransactionCycle(
            property_id=prop.id,
            listing_id="MLS-2",
            started_at=datetime(2024, 1, 5, tzinfo=UTC),
            closed_at=datetime(2024, 2, 20, tzinfo=UTC),
            outcome="CLOSED",
            termination_reason="UNKNOWN",
            reason_verification_level="UNVERIFIED",
        )
        db.add_all([terminated, closed])
        db.flush()

        response = client.get(f"/api/v1/properties/{prop.id}/transactions")

        assert response.status_code == 200
        transactions = response.json()["transactions"]
        assert [t["id"] for t in transactions] == [str(terminated.id), str(closed.id)]

        first = transactions[0]
        assert first["outcome"] == "TERMINATED"
        assert first["termination_reason"] == "UNKNOWN"
        assert first["reason_verification_level"] == "SYSTEM_INFERRED"
        assert datetime.fromisoformat(first["under_contract_at"]) == datetime(
            2023, 2, 1, tzinfo=UTC
        )
        assert first["closed_at"] is None

        second = transactions[1]
        assert second["outcome"] == "CLOSED"
        assert second["reason_verification_level"] == "UNVERIFIED"
        assert second["terminated_at"] is None


class TestSourcesEndpoint:
    def test_sources_aggregate_record_count_and_last_refreshed(
        self, client: TestClient, db: Session
    ) -> None:
        prop = make_property(db, "300 Test St")
        make_source_record(
            db, prop, source_name="hcad", source_record_id="A1",
            retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        make_source_record(
            db, prop, source_name="hcad", source_record_id="A2",
            retrieved_at=datetime(2024, 2, 1, tzinfo=UTC),
        )
        make_source_record(
            db, prop, source_name="houston_code", source_record_id="C1",
            retrieved_at=datetime(2024, 3, 5, tzinfo=UTC),
        )
        db.add(
            SourceSyncRun(
                source_name="hcad",
                status="SUCCEEDED",
                finished_at=datetime(2024, 4, 1, tzinfo=UTC),
            )
        )
        # Records for another property must not inflate this property's counts.
        other = make_property(db, "901 Other St")
        make_source_record(
            db, other, source_name="hcad", source_record_id="Z9",
            retrieved_at=datetime(2024, 5, 1, tzinfo=UTC),
        )
        db.flush()

        response = client.get(f"/api/v1/properties/{prop.id}/sources")

        assert response.status_code == 200
        sources = response.json()["sources"]
        assert [s["source"] for s in sources] == ["hcad", "houston_code"]

        hcad, houston_code = sources
        assert hcad["record_count"] == 2
        assert datetime.fromisoformat(hcad["last_refreshed"]) == datetime(
            2024, 4, 1, tzinfo=UTC
        )
        assert houston_code["record_count"] == 1
        assert datetime.fromisoformat(houston_code["last_refreshed"]) == datetime(
            2024, 3, 5, tzinfo=UTC
        )

    def test_sources_empty_for_property_without_records(
        self, client: TestClient, db: Session
    ) -> None:
        prop = make_property(db, "100 Test St")
        response = client.get(f"/api/v1/properties/{prop.id}/sources")
        assert response.status_code == 200
        assert response.json() == {"sources": []}


def test_failed_sync_run_does_not_advance_freshness(client: TestClient, db: Session) -> None:
    """A FAILED run stamps finished_at but refreshed nothing — last_refreshed
    must stay at the last SUCCESSFUL refresh (spec section 42)."""
    prop = make_property(db, "77 Freshness Ln")
    make_source_record(
        db, prop, source_name="houston_code", source_record_id="F1",
        retrieved_at=datetime(2024, 3, 5, tzinfo=UTC),
    )
    db.add(
        SourceSyncRun(
            source_name="houston_code",
            status="SUCCEEDED",
            finished_at=datetime(2024, 4, 1, tzinfo=UTC),
        )
    )
    # CKAN outage: nightly runs fail for weeks, each with a fresh finished_at.
    db.add(
        SourceSyncRun(
            source_name="houston_code",
            status="FAILED",
            finished_at=datetime(2024, 6, 15, tzinfo=UTC),
            error_message="ConnectError: CKAN unreachable",
        )
    )
    db.flush()

    detail = client.get(f"/api/v1/properties/{prop.id}").json()
    assert detail["freshness"] == [
        {"source": "houston_code", "last_refreshed": "2024-04-01T00:00:00+00:00"}
    ]
    sources = client.get(f"/api/v1/properties/{prop.id}/sources").json()["sources"]
    assert sources[0]["last_refreshed"] == "2024-04-01T00:00:00+00:00"
