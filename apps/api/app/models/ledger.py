"""Event ledger: append-only property events and typed relationships between them.

No hard deletes of history: events are retracted via retracted_at/retraction_reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class LedgerEvent(Base):
    """One event in a property's condition/transaction history."""

    __tablename__ = "ledger_events"
    __table_args__ = (
        Index("ix_ledger_events_property_id_event_date", "property_id", "event_date"),
        Index("ix_ledger_events_source_record_id", "source_record_id"),
        Index(
            "uq_ledger_dedup",
            "property_id",
            "event_type",
            "event_date",
            "source_record_id",
            unique=True,
            postgresql_where=text("source_record_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", name="fk_ledger_events_property_id"), nullable=False
    )
    # EventType values: PROPERTY_CREATED, OWNERSHIP_TRANSFER, DEED_RECORDED, LIEN_RECORDED,
    # LIEN_RELEASED, PERMIT_APPLIED, PERMIT_ISSUED, PERMIT_INSPECTION, PERMIT_FINALIZED,
    # CODE_VIOLATION_OPENED, CODE_VIOLATION_ACTION, CODE_VIOLATION_RESOLVED, LISTING_CREATED,
    # LISTING_PRICE_CHANGED, LISTING_ACTIVE, LISTING_UNDER_CONTRACT, LISTING_PENDING,
    # LISTING_BACK_ON_MARKET, LISTING_CLOSED, LISTING_WITHDRAWN, INSPECTION_PERFORMED,
    # CONDITION_FINDING, REPAIR_RECOMMENDED, TRANSACTION_TERMINATED, REPAIR_REPORTED,
    # REPAIR_VERIFIED, FINDING_DISPUTED, FINDING_RESOLVED, FINDING_REOPENED
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_records.id", name="fk_ledger_events_source_record_id"), nullable=True
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("submissions.id", name="fk_ledger_events_submission_id", use_alter=True),
        nullable=True,
    )
    # VerificationLevel values: GOVERNMENT_RECORD, LICENSED_PROFESSIONAL, TRANSACTION_DOCUMENT,
    # DOCUMENT_SUPPORTED, PARTICIPANT_REPORTED, SYSTEM_INFERRED, UNVERIFIED
    verification_level: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Visibility values: PUBLIC, PRIVATE, PENDING_REVIEW
    visibility: Mapped[str] = mapped_column(
        Text, server_default=text("'PUBLIC'"), nullable=False
    )
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retraction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EventRelationship(Base):
    """Typed directed edge between two ledger events (provenance chain)."""

    __tablename__ = "event_relationships"
    __table_args__ = (
        UniqueConstraint(
            "from_event_id",
            "to_event_id",
            "relationship_type",
            name="uq_event_relationships_from_to_type",
        ),
        Index("ix_event_relationships_from_event_id", "from_event_id"),
        Index("ix_event_relationships_to_event_id", "to_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    from_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_events.id", name="fk_event_relationships_from_event_id"),
        nullable=False,
    )
    to_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_events.id", name="fk_event_relationships_to_event_id"), nullable=False
    )
    # RelationshipType values: SAME_AS, SUPPORTS, RESOLVES, DISPUTES, SUPERSEDES, RELATED_TO
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
