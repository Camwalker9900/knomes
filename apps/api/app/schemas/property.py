"""Property response schemas. JSON field names are frozen per plan/CONTRACTS.md."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer

UUIDStr = Annotated[uuid.UUID, PlainSerializer(str, return_type=str, when_used="always")]
"""UUID field that always serializes as its canonical string form."""

MatchType = Literal["EXACT_ADDRESS", "HCAD_ID", "FUZZY_ADDRESS"]


class ConditionSummary(BaseModel):
    """Aggregate condition counts shown at the top of the property page."""

    open_findings: int
    resolved_findings: int
    disputed_findings: int
    verified_inspections: int
    prior_transactions: int


class FreshnessEntry(BaseModel):
    """Last-refresh timestamp (ISO-8601 string) for one data source."""

    source: str
    last_refreshed: str | None = None


class PropertySearchResult(BaseModel):
    """One row in the search autocomplete/results list."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDStr
    address_line1: str
    unit: str | None = None
    city: str
    state: str
    postal_code: str
    hcad_account_id: str | None = None
    match_type: MatchType


class PropertyDetail(BaseModel):
    """Full property header plus condition summary and per-source freshness."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDStr
    address_line1: str
    unit: str | None = None
    city: str
    state: str
    postal_code: str
    hcad_account_id: str | None = None
    year_built: int | None = None
    building_sqft: int | None = None
    lot_sqft: float | None = None
    property_type: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    bedrooms: int | None = None
    bathrooms_full: int | None = None
    bathrooms_half: int | None = None
    quality_code: str | None = None
    year_remodeled: int | None = None
    condition_summary: ConditionSummary
    freshness: list[FreshnessEntry]
