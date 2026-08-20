"""Properties API: search, detail, timeline, findings, transactions, sources.

Endpoint shapes are frozen in plan/CONTRACTS.md. Unknown property ids return
404; malformed UUIDs are rejected with 422 by FastAPI path typing; unknown
timeline filters are rejected with 422 by the Literal query type.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.models import Finding, FindingResolution, LedgerEvent, Property, SourceRecord
from app.models import TransactionCycle as TransactionCycleModel
from app.schemas import (
    FindingDetail,
    PropertyDetail,
    PropertySearchResult,
    Provenance,
    ResolutionOut,
    SourceProvenance,
    TimelineEvent,
    TransactionCycleOut,
)
from app.services.search import search_properties
from app.services.timeline import condition_summary, freshness, get_timeline, source_provenance

router = APIRouter(prefix="/api/v1/properties", tags=["properties"])

SessionDep = Annotated[Session, Depends(get_session)]

TimelineFilter = Literal[
    "all",
    "transactions",
    "inspections",
    "findings",
    "repairs",
    "permits",
    "code",
    "ownership",
]


def _get_property_or_404(session: Session, property_id: uuid.UUID) -> Property:
    prop = session.get(Property, property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return prop


def _timeline_event(event: LedgerEvent, record: SourceRecord | None) -> TimelineEvent:
    provenance = (
        Provenance(source_name=record.source_name, record_type=record.record_type)
        if record is not None
        else None
    )
    return TimelineEvent(
        id=event.id,
        event_type=event.event_type,
        event_date=event.event_date,
        title=event.title,
        summary=event.summary,
        verification_level=event.verification_level,
        confidence=event.confidence,
        provenance=provenance,
    )


@router.get("/search")
def search(
    session: SessionDep,
    q: Annotated[str, Query(min_length=1, description="Address or HCAD account id")],
) -> dict[str, list[PropertySearchResult]]:
    """Priority-ladder property search: exact address, HCAD id, then fuzzy. Max 10."""
    results = [
        PropertySearchResult(
            id=prop.id,
            address_line1=prop.address_line1,
            unit=prop.unit,
            city=prop.city,
            state=prop.state,
            postal_code=prop.postal_code,
            hcad_account_id=prop.hcad_account_id,
            match_type=match_type,  # type: ignore[arg-type]
        )
        for prop, match_type in search_properties(session, q)
    ]
    return {"results": results}


@router.get("/{property_id}")
def get_property(property_id: uuid.UUID, session: SessionDep) -> PropertyDetail:
    """Property header fields plus condition summary counts and per-source freshness."""
    prop = _get_property_or_404(session, property_id)
    return PropertyDetail(
        id=prop.id,
        address_line1=prop.address_line1,
        unit=prop.unit,
        city=prop.city,
        state=prop.state,
        postal_code=prop.postal_code,
        hcad_account_id=prop.hcad_account_id,
        year_built=prop.year_built,
        building_sqft=prop.building_sqft,
        lot_sqft=prop.lot_sqft,
        property_type=prop.property_type,
        latitude=prop.latitude,
        longitude=prop.longitude,
        bedrooms=prop.bedrooms,
        bathrooms_full=prop.bathrooms_full,
        bathrooms_half=prop.bathrooms_half,
        quality_code=prop.quality_code,
        year_remodeled=prop.year_remodeled,
        condition_summary=condition_summary(session, property_id),
        freshness=freshness(session, property_id),
    )


@router.get("/{property_id}/timeline")
def get_property_timeline(
    property_id: uuid.UUID,
    session: SessionDep,
    filter_: Annotated[TimelineFilter, Query(alias="filter")] = "all",
) -> dict[str, list[TimelineEvent]]:
    """Public, non-retracted events in chronological order, optionally filtered."""
    _get_property_or_404(session, property_id)
    rows = get_timeline(session, property_id, filter_)
    return {"events": [_timeline_event(event, record) for event, record in rows]}


@router.get("/{property_id}/findings")
def get_property_findings(
    property_id: uuid.UUID, session: SessionDep
) -> dict[str, list[FindingDetail]]:
    """Findings with their resolution chains (resolutions ordered by resolved_at)."""
    _get_property_or_404(session, property_id)
    findings = list(
        session.scalars(
            select(Finding)
            .where(Finding.property_id == property_id)
            .order_by(Finding.first_observed_at.asc().nulls_last(), Finding.created_at.asc())
        )
    )
    resolutions_by_finding: defaultdict[uuid.UUID, list[ResolutionOut]] = defaultdict(list)
    if findings:
        resolution_rows = session.scalars(
            select(FindingResolution)
            .where(FindingResolution.finding_id.in_([f.id for f in findings]))
            .order_by(
                FindingResolution.resolved_at.asc().nulls_last(),
                FindingResolution.created_at.asc(),
            )
        )
        for resolution in resolution_rows:
            resolutions_by_finding[resolution.finding_id].append(
                ResolutionOut.model_validate(resolution)
            )
    details = [
        FindingDetail(
            id=finding.id,
            category=finding.category,
            subcategory=finding.subcategory,
            title=finding.title,
            description=finding.description,
            severity=finding.severity,
            status=finding.status,
            first_observed_at=finding.first_observed_at,
            latest_observed_at=finding.latest_observed_at,
            resolutions=resolutions_by_finding[finding.id],
        )
        for finding in findings
    ]
    return {"findings": details}


@router.get("/{property_id}/transactions")
def get_property_transactions(
    property_id: uuid.UUID, session: SessionDep
) -> dict[str, list[TransactionCycleOut]]:
    """Transaction cycles with outcome and (never over-claimed) termination reason."""
    _get_property_or_404(session, property_id)
    cycles = session.scalars(
        select(TransactionCycleModel)
        .where(TransactionCycleModel.property_id == property_id)
        .order_by(
            TransactionCycleModel.started_at.asc().nulls_last(),
            TransactionCycleModel.created_at.asc(),
        )
    )
    return {"transactions": [TransactionCycleOut.model_validate(cycle) for cycle in cycles]}


@router.get("/{property_id}/sources")
def get_property_sources(
    property_id: uuid.UUID, session: SessionDep
) -> dict[str, list[SourceProvenance]]:
    """Per-source record counts and last-refreshed timestamps for one property."""
    _get_property_or_404(session, property_id)
    return {"sources": source_provenance(session, property_id)}
