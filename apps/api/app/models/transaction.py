"""Transaction cycles: one listing/contract arc per row, outcome never over-claimed.

A Pending->Active transition may yield outcome=TERMINATED with
termination_reason=UNKNOWN at SYSTEM_INFERRED level - never PROPERTY_CONDITION
without evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TransactionCycle(Base):
    """One transaction arc (listing -> contract -> close/termination) for a property."""

    __tablename__ = "transaction_cycles"
    __table_args__ = (Index("ix_transaction_cycles_property_id", "property_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", name="fk_transaction_cycles_property_id"), nullable=False
    )
    listing_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    under_contract_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # TransactionOutcome values: UNKNOWN, CLOSED, TERMINATED, WITHDRAWN, EXPIRED
    outcome: Mapped[str] = mapped_column(Text, server_default=text("'UNKNOWN'"), nullable=False)
    # TerminationReason values: UNKNOWN, PROPERTY_CONDITION, FINANCING, APPRAISAL, TITLE,
    # INSURANCE, BUYER_DECISION, SELLER_DECISION, CONTINGENCY, OTHER
    termination_reason: Mapped[str] = mapped_column(
        Text, server_default=text("'UNKNOWN'"), nullable=False
    )
    # VerificationLevel values: GOVERNMENT_RECORD, LICENSED_PROFESSIONAL, TRANSACTION_DOCUMENT,
    # DOCUMENT_SUPPORTED, PARTICIPANT_REPORTED, SYSTEM_INFERRED, UNVERIFIED
    reason_verification_level: Mapped[str] = mapped_column(
        Text, server_default=text("'UNVERIFIED'"), nullable=False
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence.id", name="fk_transaction_cycles_evidence_id", use_alter=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
