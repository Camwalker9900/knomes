"""Source layer: raw source records, sync run metrics, and record->property matches.

Raw source records are retained exactly as received; provenance is never lost.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SourceRecord(Base):
    """One raw record as received from an external source, content-addressed."""

    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "source_record_id",
            "content_hash",
            name="uq_source_records_source_record_content",
        ),
        Index("ix_source_records_source_name_retrieved_at", "source_name", "retrieved_at"),
        Index("ix_source_records_property_id", "property_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_id: Mapped[str] = mapped_column(Text, nullable=False)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("properties.id", name="fk_source_records_property_id"), nullable=True
    )
    record_type: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SourceSyncRun(Base):
    """Metrics and reproducibility metadata for one ingestion run of a source."""

    __tablename__ = "source_sync_runs"
    __table_args__ = (
        Index("ix_source_sync_runs_source_name_started_at", "source_name", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Values: RUNNING, SUCCEEDED, FAILED
    status: Mapped[str] = mapped_column(
        Text, server_default=text("'RUNNING'"), nullable=False
    )
    snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_checksum: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    records_parsed: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    records_matched: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    records_unmatched: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), nullable=False
    )
    records_rejected: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), nullable=False
    )
    source_records_created: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), nullable=False
    )
    events_created: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class RecordPropertyMatch(Base):
    """Outcome of matching one source record to a property (or the unmatched queue)."""

    __tablename__ = "record_property_matches"
    __table_args__ = (
        Index("ix_record_property_matches_source_record_id", "source_record_id"),
        Index("ix_record_property_matches_property_id", "property_id"),
        Index("ix_record_property_matches_review_status", "review_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_records.id", name="fk_record_property_matches_source_record_id"),
        nullable=False,
    )
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("properties.id", name="fk_record_property_matches_property_id"), nullable=True
    )
    # MatchMethod values: HCAD_ID, EXACT_ADDRESS, ADDRESS_ALIAS, PARCEL_GEOMETRY,
    # FUZZY_ADDRESS, MANUAL (NULL for unmatched records; see app/enums.py).
    match_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # MatchReviewStatus values: AUTO_ACCEPTED, REVIEW_REQUIRED, APPROVED, REJECTED, UNMATCHED
    review_status: Mapped[str] = mapped_column(Text, nullable=False)
    match_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
