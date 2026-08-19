"""Evidence layer: uploaded documents, participant submissions, and professionals.

Documents live in object storage (storage_key), never in PostgreSQL. Uploads are
PRIVATE by default; derived facts become public only after review.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Submission(Base):
    """A participant-submitted package of statements/documents about a property."""

    __tablename__ = "submissions"
    __table_args__ = (Index("ix_submissions_property_id", "property_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", name="fk_submissions_property_id"), nullable=False
    )
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # SubmitterRole values: BUYER, SELLER, INSPECTOR, HVAC_CONTRACTOR, ELECTRICIAN, PLUMBER,
    # ROOFER, ENGINEER, REAL_ESTATE_AGENT, OTHER
    submitter_role: Mapped[str] = mapped_column(Text, nullable=False)
    transaction_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submission_type: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ReviewStatus values: PENDING_REVIEW, APPROVED, REJECTED, NEEDS_CLARIFICATION
    status: Mapped[str] = mapped_column(
        Text, server_default=text("'PENDING_REVIEW'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class Evidence(Base):
    """One document in object storage backing claims/events/resolutions."""

    __tablename__ = "evidence"
    __table_args__ = (
        Index("ix_evidence_property_id", "property_id"),
        Index("ix_evidence_submission_id", "submission_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", name="fk_evidence_property_id"), nullable=False
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("submissions.id", name="fk_evidence_submission_id"), nullable=True
    )
    # EvidenceType values: INSPECTION_REPORT, HVAC_REPORT, ROOF_REPORT, PLUMBING_REPORT,
    # ELECTRICAL_REPORT, STRUCTURAL_REPORT, SEWER_SCOPE, SELLER_DISCLOSURE, TERMINATION_NOTICE,
    # REPAIR_AMENDMENT, CONTRACTOR_QUOTE, INVOICE, RECEIPT, PERMIT_DOCUMENT, PHOTO, OTHER
    evidence_type: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    document_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issuer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    issuer_license: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ReviewStatus values: PENDING_REVIEW, APPROVED, REJECTED, NEEDS_CLARIFICATION
    review_status: Mapped[str] = mapped_column(
        Text, server_default=text("'PENDING_REVIEW'"), nullable=False
    )
    # Visibility values: PUBLIC, PRIVATE, PENDING_REVIEW (uploads default PRIVATE)
    visibility: Mapped[str] = mapped_column(
        Text, server_default=text("'PRIVATE'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Professional(Base):
    """A licensed (or unverified) professional referenced by submissions/evidence."""

    __tablename__ = "professionals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str | None] = mapped_column(Text, nullable=True)
    profession_type: Mapped[str] = mapped_column(Text, nullable=False)
    license_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ProfessionalVerificationStatus values: UNVERIFIED, PENDING, VERIFIED, REJECTED
    verification_status: Mapped[str] = mapped_column(
        Text, server_default=text("'UNVERIFIED'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
