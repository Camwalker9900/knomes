"""Source adapter contract: raw snapshot -> parsed records -> normalized records.

Every external data source (HCAD, Houston code enforcement, ...) implements
:class:`SourceAdapter`. The generic runner (``app/ingestion/runner.py``) drives
the fetch -> parse -> normalize pipeline, matches each normalized record to a
canonical property, and reconciles event candidates into the ledger. Adapters
never write to the database themselves.
"""

from __future__ import annotations

import pathlib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar


@dataclass
class RawSnapshot:
    """One immutable capture of a source download, checksummed for reproducibility."""

    source_name: str
    storage_path: pathlib.Path
    checksum: str
    retrieved_at: datetime
    source_url: str | None


@dataclass
class EventCandidate:
    """A ledger event proposal derived from a source record.

    The runner only materializes candidates into ``ledger_events`` when the
    record's property match is auto-accepted.
    """

    event_type: str
    event_date: datetime
    title: str
    summary: str | None
    verification_level: str
    confidence: float | None


@dataclass
class NormalizedRecord:
    """A source record normalized to the canonical ingestion shape."""

    record_type: str
    source_record_id: str
    raw_payload: dict[str, Any]
    normalized_address: str | None
    hcad_account_id: str | None
    event_candidates: list[EventCandidate]
    raw_address: str | None = None


class SourceAdapter(ABC):
    """Pluggable adapter for one external data source."""

    source_name: ClassVar[str]
    parser_version: ClassVar[str]

    @abstractmethod
    async def fetch(self) -> RawSnapshot:
        """Download the source data and return a checksummed snapshot."""

    @abstractmethod
    def parse(self, snapshot: RawSnapshot) -> Iterator[dict[str, Any]]:
        """Stream raw records out of a snapshot without loading it fully into memory."""

    @abstractmethod
    def normalize(self, record: dict[str, Any]) -> NormalizedRecord:
        """Map one raw record to the canonical normalized shape.

        May raise for malformed records; the runner counts those as rejected
        without aborting the run.
        """
