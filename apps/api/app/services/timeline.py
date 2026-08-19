"""Timeline, condition-summary, and freshness queries for the property page.

Timeline rules (plan/CONTRACTS.md): only ``visibility=PUBLIC`` events whose
``retracted_at`` is NULL, ordered by ``event_date`` then ``created_at``, with
provenance joined from the originating source record when present.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import EventType, FindingStatus, VerificationLevel, Visibility
from app.models import Finding, LedgerEvent, SourceRecord, SourceSyncRun, TransactionCycle
from app.schemas.property import ConditionSummary, FreshnessEntry
from app.schemas.source import SourceProvenance

FILTER_GROUPS: Final[dict[str, frozenset[str]]] = {
    "transactions": frozenset(
        {
            EventType.LISTING_CREATED,
            EventType.LISTING_PRICE_CHANGED,
            EventType.LISTING_ACTIVE,
            EventType.LISTING_UNDER_CONTRACT,
            EventType.LISTING_PENDING,
            EventType.LISTING_BACK_ON_MARKET,
            EventType.LISTING_CLOSED,
            EventType.LISTING_WITHDRAWN,
            EventType.TRANSACTION_TERMINATED,
        }
    ),
    "inspections": frozenset({EventType.INSPECTION_PERFORMED}),
    "findings": frozenset(
        {
            EventType.CONDITION_FINDING,
            EventType.FINDING_DISPUTED,
            EventType.FINDING_RESOLVED,
            EventType.FINDING_REOPENED,
        }
    ),
    "repairs": frozenset(
        {
            EventType.REPAIR_RECOMMENDED,
            EventType.REPAIR_REPORTED,
            EventType.REPAIR_VERIFIED,
        }
    ),
    "permits": frozenset(
        {
            EventType.PERMIT_APPLIED,
            EventType.PERMIT_ISSUED,
            EventType.PERMIT_INSPECTION,
            EventType.PERMIT_FINALIZED,
        }
    ),
    "code": frozenset(
        {
            EventType.CODE_VIOLATION_OPENED,
            EventType.CODE_VIOLATION_ACTION,
            EventType.CODE_VIOLATION_RESOLVED,
        }
    ),
    "ownership": frozenset(
        {
            EventType.PROPERTY_CREATED,
            EventType.OWNERSHIP_TRANSFER,
            EventType.DEED_RECORDED,
            EventType.LIEN_RECORDED,
            EventType.LIEN_RELEASED,
        }
    ),
}

_OPEN_FINDING_STATUSES: Final[tuple[str, ...]] = (
    FindingStatus.OPEN,
    FindingStatus.RESOLUTION_REPORTED,
    FindingStatus.RESOLUTION_EVIDENCE_FOUND,
)

_VERIFIED_INSPECTION_LEVELS: Final[tuple[str, ...]] = (
    VerificationLevel.GOVERNMENT_RECORD,
    VerificationLevel.LICENSED_PROFESSIONAL,
    VerificationLevel.TRANSACTION_DOCUMENT,
    VerificationLevel.DOCUMENT_SUPPORTED,
)


def get_timeline(
    session: Session, property_id: uuid.UUID, filter_: str = "all"
) -> list[tuple[LedgerEvent, SourceRecord | None]]:
    """Public, non-retracted events for a property with their source records.

    Raises ``ValueError`` for a filter name outside the contract's set.
    """
    stmt = (
        select(LedgerEvent, SourceRecord)
        .outerjoin(SourceRecord, LedgerEvent.source_record_id == SourceRecord.id)
        .where(
            LedgerEvent.property_id == property_id,
            LedgerEvent.visibility == Visibility.PUBLIC.value,
            LedgerEvent.retracted_at.is_(None),
        )
    )
    if filter_ != "all":
        group = FILTER_GROUPS.get(filter_)
        if group is None:
            raise ValueError(f"unknown timeline filter: {filter_!r}")
        stmt = stmt.where(LedgerEvent.event_type.in_(group))
    stmt = stmt.order_by(LedgerEvent.event_date.asc(), LedgerEvent.created_at.asc())
    return [(event, record) for event, record in session.execute(stmt).all()]


def condition_summary(session: Session, property_id: uuid.UUID) -> ConditionSummary:
    """Counts for the property page header (definitions per plan Task C4)."""

    def count_findings(statuses: tuple[str, ...]) -> int:
        value = session.scalar(
            select(func.count())
            .select_from(Finding)
            .where(Finding.property_id == property_id, Finding.status.in_(statuses))
        )
        return int(value or 0)

    verified_inspections = session.scalar(
        select(func.count())
        .select_from(LedgerEvent)
        .where(
            LedgerEvent.property_id == property_id,
            LedgerEvent.event_type == EventType.INSPECTION_PERFORMED.value,
            LedgerEvent.visibility == Visibility.PUBLIC.value,
            LedgerEvent.retracted_at.is_(None),
            LedgerEvent.verification_level.in_(_VERIFIED_INSPECTION_LEVELS),
        )
    )
    prior_transactions = session.scalar(
        select(func.count())
        .select_from(TransactionCycle)
        .where(TransactionCycle.property_id == property_id)
    )
    return ConditionSummary(
        open_findings=count_findings(_OPEN_FINDING_STATUSES),
        resolved_findings=count_findings((FindingStatus.RESOLVED,)),
        disputed_findings=count_findings((FindingStatus.DISPUTED,)),
        verified_inspections=int(verified_inspections or 0),
        prior_transactions=int(prior_transactions or 0),
    )


def _sync_run_freshness(session: Session, source_names: list[str]) -> dict[str, datetime]:
    """Latest SUCCESSFUL sync-run timestamp per source.

    FAILED runs also stamp finished_at, but they refreshed nothing — counting
    them would keep "last refreshed" advancing through an outage while the
    data goes stale.
    """
    if not source_names:
        return {}
    rows = session.execute(
        select(SourceSyncRun.source_name, func.max(SourceSyncRun.finished_at))
        .where(
            SourceSyncRun.source_name.in_(source_names),
            SourceSyncRun.finished_at.is_not(None),
            SourceSyncRun.status == "SUCCEEDED",
        )
        .group_by(SourceSyncRun.source_name)
    ).all()
    return {name: finished_at for name, finished_at in rows}


def _last_refreshed(
    max_retrieved_at: datetime | None, run_finished_at: datetime | None
) -> str | None:
    candidates = [d for d in (max_retrieved_at, run_finished_at) if d is not None]
    return max(candidates).isoformat() if candidates else None


def freshness(session: Session, property_id: uuid.UUID | None = None) -> list[FreshnessEntry]:
    """Per-source last-refresh timestamps, optionally scoped to one property."""
    stmt = select(SourceRecord.source_name, func.max(SourceRecord.retrieved_at)).group_by(
        SourceRecord.source_name
    )
    if property_id is not None:
        stmt = stmt.where(SourceRecord.property_id == property_id)
    rows = session.execute(stmt).all()
    run_freshness = _sync_run_freshness(session, [name for name, _ in rows])
    return [
        FreshnessEntry(
            source=name,
            last_refreshed=_last_refreshed(max_retrieved_at, run_freshness.get(name)),
        )
        for name, max_retrieved_at in sorted(rows, key=lambda row: row[0])
    ]


def source_provenance(session: Session, property_id: uuid.UUID) -> list[SourceProvenance]:
    """Per-source record counts and last-refresh timestamps for one property."""
    rows = session.execute(
        select(
            SourceRecord.source_name,
            func.count(SourceRecord.id),
            func.max(SourceRecord.retrieved_at),
        )
        .where(SourceRecord.property_id == property_id)
        .group_by(SourceRecord.source_name)
    ).all()
    run_freshness = _sync_run_freshness(session, [name for name, _, _ in rows])
    return [
        SourceProvenance(
            source=name,
            record_count=int(record_count),
            last_refreshed=_last_refreshed(max_retrieved_at, run_freshness.get(name)),
        )
        for name, record_count, max_retrieved_at in sorted(rows, key=lambda row: row[0])
    ]
