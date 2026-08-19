"""Tests for the synthetic demo seed (plan Task C3).

Covers: idempotency by natural keys, the 100 Test Street resolved-finding arc,
the 200 Test Street SYSTEM_INFERRED termination wording, the 300 Test Street
dispute with both sides retained, and the government-record provenance invariant.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import (
    ClaimantType,
    EventType,
    FindingCategory,
    FindingStatus,
    RelationshipType,
    ResolutionType,
    ReviewStatus,
    TerminationReason,
    TransactionOutcome,
    VerificationLevel,
)
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
from app.seed.synthetic import SOURCE_NAME, seed

_COUNTED_MODELS = (
    Property,
    PropertyAddress,
    SourceRecord,
    LedgerEvent,
    Finding,
    FindingResolution,
    TransactionCycle,
    EventRelationship,
    Claim,
)


def _counts(db: Session) -> dict[str, int]:
    return {
        model.__tablename__: db.execute(
            select(func.count()).select_from(model)
        ).scalar_one()
        for model in _COUNTED_MODELS
    }


def _property(db: Session, hcad_account_id: str) -> Property:
    return db.execute(
        select(Property).where(Property.hcad_account_id == hcad_account_id)
    ).scalar_one()


def test_seed_twice_is_idempotent(db: Session) -> None:
    seed(db)
    first = _counts(db)
    seed(db)
    second = _counts(db)

    assert first == second
    assert first["properties"] == 3
    assert first["ledger_events"] > 0
    assert first["source_records"] > 0
    for hcad_id in ("TEST000100", "TEST000200", "TEST000300"):
        assert _property(db, hcad_id).normalized_address.endswith("TEST ST")


def test_100_test_street_finding_resolved_via_permit_finalized(db: Session) -> None:
    seed(db)
    prop = _property(db, "TEST000100")

    finding = db.execute(
        select(Finding).where(Finding.property_id == prop.id)
    ).scalar_one()
    assert finding.category == FindingCategory.HVAC
    assert finding.title == "HVAC replacement recommended"
    assert finding.status == FindingStatus.RESOLVED

    resolution = db.execute(
        select(FindingResolution).where(FindingResolution.finding_id == finding.id)
    ).scalar_one()
    assert resolution.resolution_type == ResolutionType.PERMIT_FINALIZATION
    assert resolution.verification_level == VerificationLevel.GOVERNMENT_RECORD
    assert resolution.event_id is not None

    resolving_event = db.get(LedgerEvent, resolution.event_id)
    assert resolving_event is not None
    assert resolving_event.event_type == EventType.PERMIT_FINALIZED
    assert resolving_event.property_id == prop.id

    cycle = db.execute(
        select(TransactionCycle).where(TransactionCycle.property_id == prop.id)
    ).scalar_one()
    assert cycle.outcome == TransactionOutcome.TERMINATED
    assert cycle.termination_reason == TerminationReason.PROPERTY_CONDITION
    assert cycle.reason_verification_level == VerificationLevel.TRANSACTION_DOCUMENT

    finding_event = db.execute(
        select(LedgerEvent).where(
            LedgerEvent.property_id == prop.id,
            LedgerEvent.event_type == EventType.CONDITION_FINDING,
        )
    ).scalar_one()
    resolves = db.execute(
        select(EventRelationship).where(
            EventRelationship.from_event_id == resolving_event.id,
            EventRelationship.to_event_id == finding_event.id,
            EventRelationship.relationship_type == RelationshipType.RESOLVES,
        )
    ).scalar_one_or_none()
    assert resolves is not None


def test_200_test_street_inferred_termination_never_overclaims(db: Session) -> None:
    seed(db)
    prop = _property(db, "TEST000200")

    cycle = db.execute(
        select(TransactionCycle).where(TransactionCycle.property_id == prop.id)
    ).scalar_one()
    assert cycle.outcome == TransactionOutcome.TERMINATED
    assert cycle.termination_reason == TerminationReason.UNKNOWN
    assert cycle.reason_verification_level == VerificationLevel.SYSTEM_INFERRED

    event = db.execute(
        select(LedgerEvent).where(
            LedgerEvent.property_id == prop.id,
            LedgerEvent.event_type == EventType.TRANSACTION_TERMINATED,
        )
    ).scalar_one()
    assert event.verification_level == VerificationLevel.SYSTEM_INFERRED
    assert event.confidence == 0.75
    assert event.summary == (
        "A previous contract appears not to have closed. Reason not verified."
    )


def test_300_test_street_disputed_finding_retains_both_sides(db: Session) -> None:
    seed(db)
    prop = _property(db, "TEST000300")

    finding = db.execute(
        select(Finding).where(Finding.property_id == prop.id)
    ).scalar_one()
    assert finding.category == FindingCategory.FOUNDATION
    assert finding.title == "Foundation repair recommended"
    assert finding.status == FindingStatus.DISPUTED

    claim = db.execute(
        select(Claim).where(Claim.property_id == prop.id)
    ).scalar_one()
    assert claim.claimant_type == ClaimantType.ENGINEER
    assert claim.statement == "No repair recommended."
    assert claim.review_status == ReviewStatus.APPROVED

    # Both sides retained: the original finding event and the dispute event coexist,
    # and neither is retracted.
    finding_event = db.execute(
        select(LedgerEvent).where(
            LedgerEvent.property_id == prop.id,
            LedgerEvent.event_type == EventType.CONDITION_FINDING,
        )
    ).scalar_one()
    disputed_event = db.execute(
        select(LedgerEvent).where(
            LedgerEvent.property_id == prop.id,
            LedgerEvent.event_type == EventType.FINDING_DISPUTED,
        )
    ).scalar_one()
    assert finding_event.retracted_at is None
    assert disputed_event.retracted_at is None


def test_every_government_record_event_has_source_record(db: Session) -> None:
    seed(db)
    seeded_property_ids = db.execute(
        select(Property.id).where(Property.hcad_account_id.like("TEST000%"))
    ).scalars().all()

    government_events = db.execute(
        select(LedgerEvent).where(
            LedgerEvent.property_id.in_(seeded_property_ids),
            LedgerEvent.verification_level == VerificationLevel.GOVERNMENT_RECORD,
        )
    ).scalars().all()
    assert government_events, "seed should create government-record events"
    for event in government_events:
        assert event.source_record_id is not None, (
            f"{event.event_type} {event.title!r} lacks a source record"
        )
        record = db.get(SourceRecord, event.source_record_id)
        assert record is not None
        assert record.source_name == SOURCE_NAME
        assert record.content_hash


def test_all_seeded_events_trace_to_synthetic_source_records(db: Session) -> None:
    seed(db)
    seeded_property_ids = db.execute(
        select(Property.id).where(Property.hcad_account_id.like("TEST000%"))
    ).scalars().all()

    events = db.execute(
        select(LedgerEvent).where(LedgerEvent.property_id.in_(seeded_property_ids))
    ).scalars().all()
    assert events
    for event in events:
        assert event.source_record_id is not None
        record = db.get(SourceRecord, event.source_record_id)
        assert record is not None
        assert record.source_name == SOURCE_NAME
