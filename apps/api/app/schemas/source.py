"""Source provenance response schemas. JSON field names are frozen per plan/CONTRACTS.md."""

from __future__ import annotations

from pydantic import BaseModel


class SourceProvenance(BaseModel):
    """Per-source aggregate: how many records and when the source was last refreshed."""

    source: str
    record_count: int
    last_refreshed: str | None = None
