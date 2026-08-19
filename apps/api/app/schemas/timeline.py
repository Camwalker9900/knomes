"""Timeline response schemas. JSON field names are frozen per plan/CONTRACTS.md."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

UUIDStr = Annotated[uuid.UUID, PlainSerializer(str, return_type=str, when_used="always")]


class Provenance(BaseModel):
    """Where an event came from: the source and the type of record within it."""

    source_name: str
    record_type: str


class TimelineEvent(BaseModel):
    """One public ledger event rendered on the property timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDStr
    event_type: str
    event_date: datetime
    title: str
    summary: str | None = None
    verification_level: str
    confidence: float | None = None
    provenance: Provenance | None = None
