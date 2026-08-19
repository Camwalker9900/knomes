"""Finding response schemas. JSON field names are frozen per plan/CONTRACTS.md."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

UUIDStr = Annotated[uuid.UUID, PlainSerializer(str, return_type=str, when_used="always")]


class ResolutionOut(BaseModel):
    """An approved resolution attached to a finding."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDStr
    resolution_type: str
    description: str | None = None
    verification_level: str
    resolved_at: datetime | None = None
    event_id: UUIDStr | None = None


class FindingDetail(BaseModel):
    """One condition finding with its resolution chain."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDStr
    category: str
    subcategory: str | None = None
    title: str
    description: str | None = None
    severity: str | None = None
    status: str
    first_observed_at: datetime | None = None
    latest_observed_at: datetime | None = None
    resolutions: list[ResolutionOut] = []
