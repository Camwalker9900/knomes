"""Tests for the resolution matching engine (spec section 32, propose-only)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import (
    CandidateStatus,
    EventType,
    FindingCategory,
    FindingStatus,
    VerificationLevel,
)
from app.lib.address import address_hash, normalize_address
from app.models import AuditLog, Finding, LedgerEvent, Property, ResolutionCandidate
from app.services.resolution import find_candidate_resolutions

FIRST_OBSERVED = datetime(2026, 1, 9, tzinfo=UTC)


def _make_property(db: Session) -> Property:
    raw = f"{uuid.uuid4().hex[:8]} Test Street"
    normalized = normalize_address(raw)
    prop = Property(
        address_line1=raw,
        city="Houston",
        state="TX",
        postal_code="77000",
        normalized_address=normalized,
        address_hash=address_hash(normalized),
    )
    db.add(prop)
    db.flush()
    return prop


def _make_finding(
    db: Session,
    prop: Property,
    category: str = FindingCategory.HVAC.value,
    first_observed_at: datetime | None = FIRST_OBSERVED,
) -> Finding:
    finding = Finding(
        property_id=prop.id,
        category=category,
        title=f"{category} issue observed during inspection",
        status=FindingStatus.OPEN.value,
        first_observed_at=first_observed_at,
        latest_observed_at=first_observed_at,
    )
    db.add(finding)
    db.flush()
    return finding


def _make_permit_event(
    db: Session,
    prop: Property,
    event_type: str,
    title: str,
    event_date: datetime,
    summary: str | None = None,
) -> LedgerEvent:
    event = LedgerEvent(
        property_id=prop.id,
        event_type=event_type,
        event_date=event_date,
        title=title,
        summary=summary,
        verification_level=VerificationLevel.GOVERNMENT_RECORD.value,
        confidence=1.0,
    )
    db.add(event)
    db.flush()
    return event


def _candidate_count(db: Session, finding: Finding) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ResolutionCandidate)
            .where(ResolutionCandidate.finding_id == finding.id)
        )
        or 0
    )


def test_hvac_finding_matches_later_hvac_permit_finalized(db: Session) -> None:
    prop = _make_property(db)
    finding = _make_finding(db, prop, category=FindingCategory.HVAC.value)
    event = _make_permit_event(
        db,
        prop,
        EventType.PERMIT_FINALIZED.value,
        "HVAC replacement permit finalized",
        FIRST_OBSERVED + timedelta(days=30),
    )

    candidates = find_candidate_resolutions(db, finding)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_event_id == event.id
    assert candidate.status == CandidateStatus.REVIEW_REQUIRED.value
    assert candidate.match_score >= 0.9
    # 0.9 base + 0.05 recency bonus, capped at 0.95.
    assert candidate.match_score == pytest.approx(0.95)
    assert "HVAC" in candidate.rule
    # Persisted, not just returned.
    assert _candidate_count(db, finding) == 1


def test_mechanical_permit_token_matches_hvac_finding(db: Session) -> None:
    prop = _make_property(db)
    finding = _make_finding(db, prop, category=FindingCategory.HVAC.value)
    event = _make_permit_event(
        db,
        prop,
        EventType.PERMIT_ISSUED.value,
        "Mechanical permit issued",
        FIRST_OBSERVED + timedelta(days=20),
    )

    candidates = find_candidate_resolutions(db, finding)

    assert len(candidates) == 1
    assert candidates[0].candidate_event_id == event.id
    assert candidates[0].match_score == pytest.approx(0.8)  # 0.75 + 0.05 recency


def test_roof_finding_does_not_match_hvac_permit(db: Session) -> None:
    """Spec section 50: an HVAC permit must never close a ROOF finding."""
    prop = _make_property(db)
    roof_finding = _make_finding(db, prop, category=FindingCategory.ROOF.value)
    _make_permit_event(
        db,
        prop,
        EventType.PERMIT_FINALIZED.value,
        "HVAC replacement permit finalized",
        FIRST_OBSERVED + timedelta(days=30),
    )

    candidates = find_candidate_resolutions(db, roof_finding)

    assert candidates == []
    assert _candidate_count(db, roof_finding) == 0


def test_plumbing_finding_matches_plumbing_permit_issued(db: Session) -> None:
    prop = _make_property(db)
    finding = _make_finding(db, prop, category=FindingCategory.PLUMBING.value)
    event = _make_permit_event(
        db,
        prop,
        EventType.PERMIT_ISSUED.value,
        "Plumbing permit issued",
        FIRST_OBSERVED + timedelta(days=15),
    )

    candidates = find_candidate_resolutions(db, finding)

    assert len(candidates) == 1
    assert candidates[0].candidate_event_id == event.id
    assert candidates[0].status == CandidateStatus.REVIEW_REQUIRED.value
    assert candidates[0].match_score >= 0.75


def test_permit_before_finding_date_is_not_matched(db: Session) -> None:
    prop = _make_property(db)
    finding = _make_finding(db, prop, category=FindingCategory.HVAC.value)
    _make_permit_event(
        db,
        prop,
        EventType.PERMIT_FINALIZED.value,
        "HVAC replacement permit finalized",
        FIRST_OBSERVED - timedelta(days=10),
    )

    candidates = find_candidate_resolutions(db, finding)

    assert candidates == []
    assert _candidate_count(db, finding) == 0


def test_no_recency_bonus_beyond_180_days(db: Session) -> None:
    prop = _make_property(db)
    finding = _make_finding(db, prop, category=FindingCategory.HVAC.value)
    _make_permit_event(
        db,
        prop,
        EventType.PERMIT_FINALIZED.value,
        "HVAC replacement permit finalized",
        FIRST_OBSERVED + timedelta(days=200),
    )

    candidates = find_candidate_resolutions(db, finding)

    assert len(candidates) == 1
    assert candidates[0].match_score == pytest.approx(0.9)


def test_finding_status_is_never_mutated(db: Session) -> None:
    """Spec section 13: resolution is a human decision; the engine only proposes."""
    prop = _make_property(db)
    finding = _make_finding(db, prop, category=FindingCategory.HVAC.value)
    _make_permit_event(
        db,
        prop,
        EventType.PERMIT_FINALIZED.value,
        "HVAC replacement permit finalized",
        FIRST_OBSERVED + timedelta(days=30),
    )

    candidates = find_candidate_resolutions(db, finding)

    assert len(candidates) == 1
    assert finding.status == FindingStatus.OPEN.value
    db.expire(finding)  # reload from the database to catch flushed mutations
    assert finding.status == FindingStatus.OPEN.value


def test_duplicate_invocation_does_not_duplicate_candidates(db: Session) -> None:
    prop = _make_property(db)
    finding = _make_finding(db, prop, category=FindingCategory.HVAC.value)
    _make_permit_event(
        db,
        prop,
        EventType.PERMIT_FINALIZED.value,
        "HVAC replacement permit finalized",
        FIRST_OBSERVED + timedelta(days=30),
    )

    first = find_candidate_resolutions(db, finding)
    second = find_candidate_resolutions(db, finding)

    assert len(first) == 1
    assert second == []
    assert _candidate_count(db, finding) == 1
    audit_count = db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.entity_type == "resolution_candidates",
            AuditLog.entity_id == first[0].id,
        )
    )
    assert audit_count == 1


def test_audit_log_row_written_on_candidate_creation(db: Session) -> None:
    prop = _make_property(db)
    finding = _make_finding(db, prop, category=FindingCategory.HVAC.value)
    event = _make_permit_event(
        db,
        prop,
        EventType.PERMIT_FINALIZED.value,
        "HVAC replacement permit finalized",
        FIRST_OBSERVED + timedelta(days=30),
    )

    candidates = find_candidate_resolutions(db, finding)

    assert len(candidates) == 1
    candidate = candidates[0]
    audit = db.scalars(
        select(AuditLog).where(
            AuditLog.entity_type == "resolution_candidates",
            AuditLog.entity_id == candidate.id,
        )
    ).one()
    assert audit.actor_type == "system"
    assert audit.action == "resolution_candidate_proposed"
    assert audit.new_value is not None
    assert audit.new_value["match_score"] == pytest.approx(candidate.match_score)
    assert audit.new_value["rule"] == candidate.rule
    assert audit.new_value["finding_id"] == str(finding.id)
    assert audit.new_value["candidate_event_id"] == str(event.id)


def test_finding_without_first_observed_at_yields_no_candidates(db: Session) -> None:
    prop = _make_property(db)
    finding = _make_finding(
        db, prop, category=FindingCategory.HVAC.value, first_observed_at=None
    )
    _make_permit_event(
        db,
        prop,
        EventType.PERMIT_FINALIZED.value,
        "HVAC replacement permit finalized",
        FIRST_OBSERVED + timedelta(days=30),
    )

    assert find_candidate_resolutions(db, finding) == []
    assert _candidate_count(db, finding) == 0


def test_summary_free_text_cannot_cross_propose_categories(db: Session) -> None:
    """An HVAC permit whose free-text summary mentions the roof must not be
    proposed against a ROOF finding — keyword matching is title-only."""
    prop = _make_property(db)
    finding = _make_finding(
        db, prop, category="ROOF", first_observed_at=datetime(2026, 1, 9, tzinfo=UTC)
    )
    _make_permit_event(
        db,
        prop,
        event_type="PERMIT_FINALIZED",
        title="Mechanical permit finalized",
        event_date=datetime(2026, 2, 8, tzinfo=UTC),
        summary="Replaced HVAC ducts routed near the roof line",
    )
    candidates = find_candidate_resolutions(db, finding)
    assert candidates == []
    assert _candidate_count(db, finding) == 0
