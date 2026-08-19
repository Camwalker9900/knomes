"""Condition aggregate: claims, findings, resolutions, and proposed resolution candidates.

The system proposes resolution candidates; a human approves. A candidate never
mutates a finding's status on its own.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Claim(Base):
    """A statement asserted by a transaction participant or professional."""

    __tablename__ = "claims"
    __table_args__ = (Index("ix_claims_property_id", "property_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", name="fk_claims_property_id"), nullable=False
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("submissions.id", name="fk_claims_submission_id", use_alter=True),
        nullable=True,
    )
    # ClaimantType values: BUYER, SELLER, INSPECTOR, CONTRACTOR, ENGINEER, AGENT, OTHER
    claimant_type: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    claim_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ReviewStatus values: PENDING_REVIEW, APPROVED, REJECTED, NEEDS_CLARIFICATION
    review_status: Mapped[str] = mapped_column(
        Text, server_default=text("'PENDING_REVIEW'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Finding(Base):
    """A distinct property-condition issue tracked across its lifecycle."""

    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_property_id_status", "property_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", name="fk_findings_property_id"), nullable=False
    )
    # FindingCategory values: ROOF, HVAC, ELECTRICAL, PLUMBING, SEWER, FOUNDATION, STRUCTURAL,
    # WATER_INTRUSION, DRAINAGE, MOLD, PEST, POOL, APPLIANCE, WINDOWS, EXTERIOR, INTERIOR,
    # SAFETY, OTHER
    category: Mapped[str] = mapped_column(Text, nullable=False)
    subcategory: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FindingSeverity values: MINOR, MODERATE, MAJOR, SAFETY
    severity: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FindingStatus values: OPEN, RESOLUTION_REPORTED, RESOLUTION_EVIDENCE_FOUND, RESOLVED,
    # DISPUTED, SUPERSEDED, UNKNOWN (never "FIXED")
    status: Mapped[str] = mapped_column(Text, server_default=text("'OPEN'"), nullable=False)
    first_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latest_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_from_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("claims.id", name="fk_findings_created_from_claim_id", use_alter=True),
        nullable=True,
    )
    created_from_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ledger_events.id", name="fk_findings_created_from_event_id", use_alter=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class FindingResolution(Base):
    """An approved resolution of a finding, linked to its supporting evidence/event."""

    __tablename__ = "finding_resolutions"
    __table_args__ = (Index("ix_finding_resolutions_finding_id", "finding_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("findings.id", name="fk_finding_resolutions_finding_id"), nullable=False
    )
    # ResolutionType values: REPAIR, REPLACEMENT, PROFESSIONAL_REEVALUATION,
    # PERMIT_FINALIZATION, SELLER_DOCUMENTATION, INSPECTION_CLEARANCE, OTHER
    resolution_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # VerificationLevel values: GOVERNMENT_RECORD, LICENSED_PROFESSIONAL, TRANSACTION_DOCUMENT,
    # DOCUMENT_SUPPORTED, PARTICIPANT_REPORTED, SYSTEM_INFERRED, UNVERIFIED
    verification_level: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence.id", name="fk_finding_resolutions_evidence_id", use_alter=True),
        nullable=True,
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ledger_events.id", name="fk_finding_resolutions_event_id", use_alter=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ResolutionCandidate(Base):
    """A machine-proposed (never auto-applied) link between a finding and an event."""

    __tablename__ = "resolution_candidates"
    __table_args__ = (
        Index("ix_resolution_candidates_finding_id", "finding_id"),
        Index("ix_resolution_candidates_candidate_event_id", "candidate_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("findings.id", name="fk_resolution_candidates_finding_id"), nullable=False
    )
    candidate_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_events.id", name="fk_resolution_candidates_candidate_event_id"),
        nullable=False,
    )
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    # CandidateStatus values: REVIEW_REQUIRED, APPROVED, REJECTED
    status: Mapped[str] = mapped_column(
        Text, server_default=text("'REVIEW_REQUIRED'"), nullable=False
    )
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
