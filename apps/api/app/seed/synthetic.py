"""Three synthetic Houston properties exercising the ledger arcs (plan Task C3, spec §65).

- 100 Test Street: the full §65 arc — inspection finding (HVAC), transaction terminated
  for property condition, later mechanical permit issued and finalized, finding RESOLVED
  with a government-record-verified resolution linked to the permit-finalized event.
- 200 Test Street: pending -> back on market with a SYSTEM_INFERRED termination.
  Reason stays UNKNOWN; the public wording never claims more than the evidence supports.
- 300 Test Street: a foundation finding DISPUTED by a licensed engineer. Both sides
  are retained; nothing is deleted or over-claimed.

Every seeded ledger event traces to a ``source_records`` row
(``source_name='synthetic_fixture'``); government-record events always carry one.
The seed is idempotent: rows are upserted by natural keys, so re-running converges
on the same dataset (row counts do not change).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import (
    ClaimantType,
    EventType,
    FindingCategory,
    FindingSeverity,
    FindingStatus,
    RelationshipType,
    ResolutionType,
    ReviewStatus,
    TerminationReason,
    TransactionOutcome,
    VerificationLevel,
    Visibility,
)
from app.lib.address import address_hash, normalize_address
from app.models import (
    Claim,
    EventRelationship,
    Finding,
    FindingResolution,
    LedgerEvent,
    Property,
    PropertyAddress,
    SourceRecord,
    TransactionCycle,
)

SOURCE_NAME = "synthetic_fixture"
PARSER_VERSION = "seed-c3-1"

# §33 public wording for an inferred (unverified) termination — exact sentence.
INFERRED_TERMINATION_SUMMARY = (
    "A previous contract appears not to have closed. Reason not verified."
)


def _dt(month: int, day: int) -> datetime:
    """A UTC midnight timestamp in 2026 (all fixture dates are in 2026)."""
    return datetime(2026, month, day, tzinfo=UTC)


def _content_hash(raw_payload: dict[str, Any]) -> str:
    """sha256 hexdigest of the canonical JSON serialization of a raw payload."""
    canonical = json.dumps(raw_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ensure_property(
    session: Session,
    *,
    hcad_account_id: str,
    address_line1: str,
    year_built: int,
    building_sqft: int,
    lot_sqft: float,
) -> Property:
    """Upsert a property by its HCAD account id and its fixture address alias."""
    prop = session.execute(
        select(Property).where(Property.hcad_account_id == hcad_account_id)
    ).scalar_one_or_none()
    if prop is None:
        prop = Property(hcad_account_id=hcad_account_id)
        session.add(prop)
    normalized = normalize_address(address_line1)
    prop.address_line1 = address_line1
    prop.city = "Houston"
    prop.state = "TX"
    prop.postal_code = "77000"
    prop.normalized_address = normalized
    prop.address_hash = address_hash(normalized)
    prop.year_built = year_built
    prop.building_sqft = building_sqft
    prop.lot_sqft = lot_sqft
    prop.property_type = "SINGLE_FAMILY"
    session.flush()
    _ensure_alias(session, prop, raw_address=address_line1)
    return prop


def _ensure_alias(session: Session, prop: Property, *, raw_address: str) -> PropertyAddress:
    """Upsert a property_addresses alias row by (property_id, raw_address)."""
    alias = session.execute(
        select(PropertyAddress).where(
            PropertyAddress.property_id == prop.id,
            PropertyAddress.raw_address == raw_address,
        )
    ).scalar_one_or_none()
    if alias is None:
        alias = PropertyAddress(property_id=prop.id, raw_address=raw_address)
        session.add(alias)
    alias.normalized_address = normalize_address(raw_address)
    alias.source = SOURCE_NAME
    alias.is_current = True
    session.flush()
    return alias


def _ensure_source_record(
    session: Session,
    prop: Property,
    *,
    source_record_id: str,
    record_type: str,
    raw_payload: dict[str, Any],
    source_date: datetime,
) -> SourceRecord:
    """Upsert a source record by (source_name, source_record_id, content_hash)."""
    content_hash = _content_hash(raw_payload)
    record = session.execute(
        select(SourceRecord).where(
            SourceRecord.source_name == SOURCE_NAME,
            SourceRecord.source_record_id == source_record_id,
            SourceRecord.content_hash == content_hash,
        )
    ).scalar_one_or_none()
    if record is None:
        record = SourceRecord(
            source_name=SOURCE_NAME,
            source_record_id=source_record_id,
            content_hash=content_hash,
            record_type=record_type,
            raw_payload=raw_payload,
        )
        session.add(record)
    record.property_id = prop.id
    record.record_type = record_type
    record.raw_payload = raw_payload
    record.source_date = source_date
    record.parser_version = PARSER_VERSION
    session.flush()
    return record


def _ensure_event(
    session: Session,
    prop: Property,
    *,
    event_type: EventType,
    event_date: datetime,
    title: str,
    verification_level: VerificationLevel,
    source_record: SourceRecord,
    summary: str | None = None,
    confidence: float | None = None,
) -> LedgerEvent:
    """Upsert a ledger event by (property_id, event_type, event_date, title)."""
    event = session.execute(
        select(LedgerEvent).where(
            LedgerEvent.property_id == prop.id,
            LedgerEvent.event_type == event_type,
            LedgerEvent.event_date == event_date,
            LedgerEvent.title == title,
        )
    ).scalar_one_or_none()
    if event is None:
        event = LedgerEvent(
            property_id=prop.id,
            event_type=event_type,
            event_date=event_date,
            title=title,
        )
        session.add(event)
    event.summary = summary
    event.verification_level = verification_level
    event.confidence = confidence
    event.source_record_id = source_record.id
    event.visibility = Visibility.PUBLIC
    session.flush()
    return event


def _recorded_event(
    session: Session,
    prop: Property,
    *,
    event_type: EventType,
    event_date: datetime,
    title: str,
    verification_level: VerificationLevel,
    record_id: str,
    record_type: str,
    raw_payload: dict[str, Any],
    summary: str | None = None,
    confidence: float | None = None,
) -> tuple[LedgerEvent, SourceRecord]:
    """Upsert a source record and the ledger event that traces to it."""
    record = _ensure_source_record(
        session,
        prop,
        source_record_id=record_id,
        record_type=record_type,
        raw_payload=raw_payload,
        source_date=event_date,
    )
    event = _ensure_event(
        session,
        prop,
        event_type=event_type,
        event_date=event_date,
        title=title,
        verification_level=verification_level,
        source_record=record,
        summary=summary,
        confidence=confidence,
    )
    return event, record


def _ensure_finding(
    session: Session,
    prop: Property,
    *,
    category: FindingCategory,
    title: str,
    status: FindingStatus,
    severity: FindingSeverity,
    description: str | None,
    first_observed_at: datetime,
    latest_observed_at: datetime,
    created_from_event: LedgerEvent,
) -> Finding:
    """Upsert a finding by (property_id, category, title)."""
    finding = session.execute(
        select(Finding).where(
            Finding.property_id == prop.id,
            Finding.category == category,
            Finding.title == title,
        )
    ).scalar_one_or_none()
    if finding is None:
        finding = Finding(property_id=prop.id, category=category, title=title)
        session.add(finding)
    finding.status = status
    finding.severity = severity
    finding.description = description
    finding.first_observed_at = first_observed_at
    finding.latest_observed_at = latest_observed_at
    finding.created_from_event_id = created_from_event.id
    session.flush()
    return finding


def _ensure_resolution(
    session: Session,
    finding: Finding,
    *,
    resolution_type: ResolutionType,
    verification_level: VerificationLevel,
    resolved_at: datetime,
    event: LedgerEvent,
    description: str | None,
) -> FindingResolution:
    """Upsert a finding resolution by (finding_id, resolution_type, event_id)."""
    resolution = session.execute(
        select(FindingResolution).where(
            FindingResolution.finding_id == finding.id,
            FindingResolution.resolution_type == resolution_type,
            FindingResolution.event_id == event.id,
        )
    ).scalar_one_or_none()
    if resolution is None:
        resolution = FindingResolution(
            finding_id=finding.id,
            resolution_type=resolution_type,
            event_id=event.id,
        )
        session.add(resolution)
    resolution.verification_level = verification_level
    resolution.resolved_at = resolved_at
    resolution.description = description
    session.flush()
    return resolution


def _ensure_cycle(
    session: Session,
    prop: Property,
    *,
    started_at: datetime | None,
    under_contract_at: datetime | None,
    terminated_at: datetime | None,
    outcome: TransactionOutcome,
    termination_reason: TerminationReason,
    reason_verification_level: VerificationLevel,
) -> TransactionCycle:
    """Upsert a transaction cycle by (property_id, started_at-or-under_contract_at)."""
    if started_at is not None:
        anchor = TransactionCycle.started_at == started_at
    else:
        anchor = TransactionCycle.under_contract_at == under_contract_at
    cycle = session.execute(
        select(TransactionCycle).where(TransactionCycle.property_id == prop.id, anchor)
    ).scalar_one_or_none()
    if cycle is None:
        cycle = TransactionCycle(property_id=prop.id)
        session.add(cycle)
    cycle.started_at = started_at
    cycle.under_contract_at = under_contract_at
    cycle.terminated_at = terminated_at
    cycle.closed_at = None
    cycle.outcome = outcome
    cycle.termination_reason = termination_reason
    cycle.reason_verification_level = reason_verification_level
    session.flush()
    return cycle


def _ensure_relationship(
    session: Session,
    *,
    from_event: LedgerEvent,
    to_event: LedgerEvent,
    relationship_type: RelationshipType,
) -> EventRelationship:
    """Upsert an event relationship by (from_event_id, to_event_id, relationship_type)."""
    relationship = session.execute(
        select(EventRelationship).where(
            EventRelationship.from_event_id == from_event.id,
            EventRelationship.to_event_id == to_event.id,
            EventRelationship.relationship_type == relationship_type,
        )
    ).scalar_one_or_none()
    if relationship is None:
        relationship = EventRelationship(
            from_event_id=from_event.id,
            to_event_id=to_event.id,
            relationship_type=relationship_type,
        )
        session.add(relationship)
        session.flush()
    return relationship


def _ensure_claim(
    session: Session,
    prop: Property,
    *,
    claimant_type: ClaimantType,
    statement: str,
    claim_date: datetime,
    review_status: ReviewStatus,
) -> Claim:
    """Upsert a claim by (property_id, claimant_type, statement)."""
    claim = session.execute(
        select(Claim).where(
            Claim.property_id == prop.id,
            Claim.claimant_type == claimant_type,
            Claim.statement == statement,
        )
    ).scalar_one_or_none()
    if claim is None:
        claim = Claim(property_id=prop.id, claimant_type=claimant_type, statement=statement)
        session.add(claim)
    claim.claim_date = claim_date
    claim.review_status = review_status
    session.flush()
    return claim


def _seed_100_test_street(session: Session) -> None:
    """§65 full arc: HVAC finding, condition termination, permit-verified resolution."""
    prop = _ensure_property(
        session,
        hcad_account_id="TEST000100",
        address_line1="100 Test Street",
        year_built=1998,
        building_sqft=2150,
        lot_sqft=7200.0,
    )

    _recorded_event(
        session,
        prop,
        event_type=EventType.LISTING_CREATED,
        event_date=_dt(1, 1),
        title="Listed for sale",
        summary="Property listed for sale.",
        verification_level=VerificationLevel.TRANSACTION_DOCUMENT,
        record_id="TEST000100-LISTING-2026-001",
        record_type="listing_event",
        raw_payload={
            "address": "100 Test Street",
            "hcad_account_id": "TEST000100",
            "status": "ACTIVE",
            "status_date": "2026-01-01",
        },
    )
    _recorded_event(
        session,
        prop,
        event_type=EventType.LISTING_UNDER_CONTRACT,
        event_date=_dt(1, 5),
        title="Went under contract",
        summary="A buyer went under contract on the property.",
        verification_level=VerificationLevel.TRANSACTION_DOCUMENT,
        record_id="TEST000100-LISTING-2026-002",
        record_type="listing_event",
        raw_payload={
            "address": "100 Test Street",
            "hcad_account_id": "TEST000100",
            "status": "UNDER_CONTRACT",
            "status_date": "2026-01-05",
        },
    )
    _recorded_event(
        session,
        prop,
        event_type=EventType.INSPECTION_PERFORMED,
        event_date=_dt(1, 8),
        title="General home inspection performed",
        summary="Licensed inspector performed a general home inspection.",
        verification_level=VerificationLevel.LICENSED_PROFESSIONAL,
        record_id="TEST000100-INSPECTION-2026-001",
        record_type="inspection_report",
        raw_payload={
            "address": "100 Test Street",
            "hcad_account_id": "TEST000100",
            "inspector_license": "TREC-00000",
            "inspection_date": "2026-01-08",
            "report": "General inspection; HVAC at end of service life.",
        },
    )
    finding_event, _ = _recorded_event(
        session,
        prop,
        event_type=EventType.CONDITION_FINDING,
        event_date=_dt(1, 9),
        title="HVAC replacement recommended",
        summary="Inspection found the HVAC system at end of service life.",
        verification_level=VerificationLevel.LICENSED_PROFESSIONAL,
        record_id="TEST000100-FINDING-2026-001",
        record_type="condition_finding",
        raw_payload={
            "address": "100 Test Street",
            "hcad_account_id": "TEST000100",
            "category": "HVAC",
            "observed_date": "2026-01-09",
            "finding": "HVAC system at end of service life; replacement recommended.",
        },
    )
    _recorded_event(
        session,
        prop,
        event_type=EventType.REPAIR_RECOMMENDED,
        event_date=_dt(1, 9),
        title="Repair recommended: HVAC replacement",
        summary="Licensed inspector recommended replacing the HVAC system.",
        verification_level=VerificationLevel.LICENSED_PROFESSIONAL,
        record_id="TEST000100-REPAIR-REC-2026-001",
        record_type="repair_recommendation",
        raw_payload={
            "address": "100 Test Street",
            "hcad_account_id": "TEST000100",
            "category": "HVAC",
            "recommended_date": "2026-01-09",
            "recommendation": "Replace HVAC system.",
        },
    )
    _recorded_event(
        session,
        prop,
        event_type=EventType.TRANSACTION_TERMINATED,
        event_date=_dt(1, 12),
        title="Contract terminated",
        summary="Contract terminated due to property condition (HVAC).",
        verification_level=VerificationLevel.TRANSACTION_DOCUMENT,
        record_id="TEST000100-TERMINATION-2026-001",
        record_type="termination_notice",
        raw_payload={
            "address": "100 Test Street",
            "hcad_account_id": "TEST000100",
            "terminated_date": "2026-01-12",
            "reason": "PROPERTY_CONDITION",
            "document": "Termination notice citing HVAC condition.",
        },
    )
    permit_issued_event, _ = _recorded_event(
        session,
        prop,
        event_type=EventType.PERMIT_ISSUED,
        event_date=_dt(2, 1),
        title="Mechanical permit issued",
        summary="Mechanical permit M2026-001 issued for HVAC replacement.",
        verification_level=VerificationLevel.GOVERNMENT_RECORD,
        confidence=1.0,
        record_id="TEST000100-PERMIT-M2026-001",
        record_type="permit",
        raw_payload={
            "address": "100 Test Street",
            "hcad_account_id": "TEST000100",
            "permit_number": "M2026-001",
            "permit_type": "MECHANICAL",
            "status": "ISSUED",
            "issued_date": "2026-02-01",
            "description": "Replace HVAC system.",
        },
    )
    _recorded_event(
        session,
        prop,
        event_type=EventType.REPAIR_REPORTED,
        event_date=_dt(2, 5),
        title="HVAC replacement reported complete",
        summary="Seller reported the HVAC replacement was completed.",
        verification_level=VerificationLevel.PARTICIPANT_REPORTED,
        record_id="TEST000100-REPAIR-2026-001",
        record_type="repair_report",
        raw_payload={
            "address": "100 Test Street",
            "hcad_account_id": "TEST000100",
            "reported_date": "2026-02-05",
            "reported_by": "SELLER",
            "statement": "HVAC system replaced.",
        },
    )
    permit_final_event, permit_final_record = _recorded_event(
        session,
        prop,
        event_type=EventType.PERMIT_FINALIZED,
        event_date=_dt(2, 8),
        title="Mechanical permit finalized",
        summary="Mechanical permit M2026-001 finalized after city inspection.",
        verification_level=VerificationLevel.GOVERNMENT_RECORD,
        confidence=1.0,
        record_id="TEST000100-PERMIT-M2026-001-FINAL",
        record_type="permit",
        raw_payload={
            "address": "100 Test Street",
            "hcad_account_id": "TEST000100",
            "permit_number": "M2026-001",
            "permit_type": "MECHANICAL",
            "status": "FINALED",
            "finalized_date": "2026-02-08",
            "description": "Replace HVAC system.",
        },
    )
    _ensure_event(
        session,
        prop,
        event_type=EventType.FINDING_RESOLVED,
        event_date=_dt(2, 8),
        title="HVAC finding resolved",
        summary="HVAC finding resolved via finalized mechanical permit M2026-001.",
        verification_level=VerificationLevel.GOVERNMENT_RECORD,
        source_record=permit_final_record,
    )

    finding = _ensure_finding(
        session,
        prop,
        category=FindingCategory.HVAC,
        title="HVAC replacement recommended",
        status=FindingStatus.RESOLVED,
        severity=FindingSeverity.MAJOR,
        description="HVAC system at end of service life; replacement recommended.",
        first_observed_at=_dt(1, 9),
        latest_observed_at=_dt(1, 9),
        created_from_event=finding_event,
    )
    _ensure_resolution(
        session,
        finding,
        resolution_type=ResolutionType.PERMIT_FINALIZATION,
        verification_level=VerificationLevel.GOVERNMENT_RECORD,
        resolved_at=_dt(2, 8),
        event=permit_final_event,
        description="HVAC replacement verified by finalized mechanical permit M2026-001.",
    )
    _ensure_cycle(
        session,
        prop,
        started_at=_dt(1, 1),
        under_contract_at=_dt(1, 5),
        terminated_at=_dt(1, 12),
        outcome=TransactionOutcome.TERMINATED,
        termination_reason=TerminationReason.PROPERTY_CONDITION,
        reason_verification_level=VerificationLevel.TRANSACTION_DOCUMENT,
    )
    _ensure_relationship(
        session,
        from_event=permit_final_event,
        to_event=finding_event,
        relationship_type=RelationshipType.RESOLVES,
    )
    _ensure_relationship(
        session,
        from_event=permit_issued_event,
        to_event=permit_final_event,
        relationship_type=RelationshipType.SUPPORTS,
    )


def _seed_200_test_street(session: Session) -> None:
    """Pending -> back on market: SYSTEM_INFERRED termination, reason UNKNOWN."""
    prop = _ensure_property(
        session,
        hcad_account_id="TEST000200",
        address_line1="200 Test Street",
        year_built=2005,
        building_sqft=1875,
        lot_sqft=6500.0,
    )

    _recorded_event(
        session,
        prop,
        event_type=EventType.LISTING_UNDER_CONTRACT,
        event_date=_dt(3, 3),
        title="Went under contract",
        summary="A buyer went under contract on the property.",
        verification_level=VerificationLevel.DOCUMENT_SUPPORTED,
        record_id="TEST000200-LISTING-2026-001",
        record_type="listing_event",
        raw_payload={
            "address": "200 Test Street",
            "hcad_account_id": "TEST000200",
            "status": "UNDER_CONTRACT",
            "status_date": "2026-03-03",
        },
    )
    _recorded_event(
        session,
        prop,
        event_type=EventType.LISTING_BACK_ON_MARKET,
        event_date=_dt(3, 20),
        title="Listing returned to market",
        summary="The listing returned to active status.",
        verification_level=VerificationLevel.DOCUMENT_SUPPORTED,
        record_id="TEST000200-LISTING-2026-002",
        record_type="listing_event",
        raw_payload={
            "address": "200 Test Street",
            "hcad_account_id": "TEST000200",
            "status": "BACK_ON_MARKET",
            "status_date": "2026-03-20",
        },
    )
    _recorded_event(
        session,
        prop,
        event_type=EventType.TRANSACTION_TERMINATED,
        event_date=_dt(3, 20),
        title="Contract appears not to have closed",
        summary=INFERRED_TERMINATION_SUMMARY,
        verification_level=VerificationLevel.SYSTEM_INFERRED,
        confidence=0.75,
        record_id="TEST000200-INFERENCE-2026-001",
        record_type="inferred_transaction_outcome",
        raw_payload={
            "address": "200 Test Street",
            "hcad_account_id": "TEST000200",
            "inference": "UNDER_CONTRACT followed by BACK_ON_MARKET without a closing",
            "inputs": ["TEST000200-LISTING-2026-001", "TEST000200-LISTING-2026-002"],
            "inferred_date": "2026-03-20",
        },
    )
    _ensure_cycle(
        session,
        prop,
        started_at=None,
        under_contract_at=_dt(3, 3),
        terminated_at=_dt(3, 20),
        outcome=TransactionOutcome.TERMINATED,
        termination_reason=TerminationReason.UNKNOWN,
        reason_verification_level=VerificationLevel.SYSTEM_INFERRED,
    )


def _seed_300_test_street(session: Session) -> None:
    """Foundation finding disputed by a licensed engineer; both sides retained."""
    prop = _ensure_property(
        session,
        hcad_account_id="TEST000300",
        address_line1="300 Test Street",
        year_built=1987,
        building_sqft=2400,
        lot_sqft=8100.0,
    )

    finding_event, _ = _recorded_event(
        session,
        prop,
        event_type=EventType.CONDITION_FINDING,
        event_date=_dt(4, 2),
        title="Foundation repair recommended",
        summary="Inspection recommended foundation repair after observing settlement.",
        verification_level=VerificationLevel.LICENSED_PROFESSIONAL,
        record_id="TEST000300-FINDING-2026-001",
        record_type="condition_finding",
        raw_payload={
            "address": "300 Test Street",
            "hcad_account_id": "TEST000300",
            "category": "FOUNDATION",
            "observed_date": "2026-04-02",
            "finding": "Differential settlement observed; foundation repair recommended.",
        },
    )
    _recorded_event(
        session,
        prop,
        event_type=EventType.FINDING_DISPUTED,
        event_date=_dt(5, 1),
        title="Foundation finding disputed by structural engineer",
        summary='Licensed structural engineer evaluated the foundation: "No repair recommended."',
        verification_level=VerificationLevel.LICENSED_PROFESSIONAL,
        record_id="TEST000300-DISPUTE-2026-001",
        record_type="engineer_report",
        raw_payload={
            "address": "300 Test Street",
            "hcad_account_id": "TEST000300",
            "engineer_license": "PE-00000",
            "report_date": "2026-05-01",
            "statement": "No repair recommended.",
        },
    )

    _ensure_finding(
        session,
        prop,
        category=FindingCategory.FOUNDATION,
        title="Foundation repair recommended",
        status=FindingStatus.DISPUTED,
        severity=FindingSeverity.MODERATE,
        description="Differential settlement observed; foundation repair recommended.",
        first_observed_at=_dt(4, 2),
        latest_observed_at=_dt(4, 2),
        created_from_event=finding_event,
    )
    _ensure_claim(
        session,
        prop,
        claimant_type=ClaimantType.ENGINEER,
        statement="No repair recommended.",
        claim_date=_dt(5, 1),
        review_status=ReviewStatus.APPROVED,
    )


def seed(session: Session) -> None:
    """Seed the three synthetic demo properties. Idempotent; commits the session."""
    _seed_100_test_street(session)
    _seed_200_test_street(session)
    _seed_300_test_street(session)
    session.commit()
