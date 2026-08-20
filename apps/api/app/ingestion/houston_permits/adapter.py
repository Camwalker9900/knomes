"""Houston building-permits source adapter (weekly permit activity reports).

The City of Houston publishes no per-address permit API or CKAN resource (the
portal carries only monthly aggregates). The compliant acquisition route is
the officially published weekly "Web eReport" permit activity report — an
XLSX file posted every Monday on
https://www.houstonpermittingcenter.org/sold-permits-search listing every
new-construction / remodel / change-of-use permit sold (issued) that week,
by ZIP code and street address. See ``parse.py`` for the verified layout.

``fetch`` downloads one report file when a source URL is configured
(``settings.houston_permits_source_url`` or the constructor override — the
published URL changes every week) and writes a checksummed snapshot to
``data/raw/houston_permits/``. The primary operating mode, though, is
``sync.py --file PATH`` against a manually downloaded report — the weekly
files only cover their own week, so historical backfill arrives as manually
downloaded archive files or an open-records request, ingested one file at a
time through the same path.

The adapter never writes to the database; ``app.ingestion.runner.run_sync``
drives matching, source_records, ledger events, and run metrics.
"""

from __future__ import annotations

import hashlib
import pathlib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, ClassVar, Final
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.ingestion.base import NormalizedRecord, RawSnapshot, SourceAdapter
from app.ingestion.houston_permits.normalize import normalize_houston_permit_record
from app.ingestion.houston_permits.parse import iter_report_rows

SOURCE_NAME: Final[str] = "houston_permits"
PARSER_VERSION: Final[str] = "1.0.0"

# .../apps/api/app/ingestion/houston_permits/adapter.py -> repo root is parents[5].
_REPO_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[5]
DEFAULT_RAW_DIR: Final[pathlib.Path] = _REPO_ROOT / "data" / "raw" / "houston_permits"

_ALLOWED_SUFFIXES: Final[frozenset[str]] = frozenset({".xlsx", ".csv"})


class HoustonPermitsAdapter(SourceAdapter):
    """Adapter for the City of Houston weekly permit activity report files."""

    source_name: ClassVar[str] = SOURCE_NAME
    parser_version: ClassVar[str] = PARSER_VERSION

    def __init__(
        self,
        *,
        source_url: str | None = None,
        raw_dir: pathlib.Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._source_url = source_url  # None -> settings.houston_permits_source_url
        self._raw_dir = raw_dir if raw_dir is not None else DEFAULT_RAW_DIR
        self._transport = transport  # injectable for tests; None -> real HTTP

    def _resolved_url(self) -> str:
        if self._source_url is not None and self._source_url.strip():
            return self._source_url.strip()
        return settings.houston_permits_source_url.strip()

    async def fetch(self) -> RawSnapshot:
        """Download one published report file and snapshot it, checksummed."""
        url = self._resolved_url()
        if not url:
            raise ValueError(
                "no permit report URL configured; set houston_permits_source_url (the"
                " weekly report URL changes — see"
                " https://www.houstonpermittingcenter.org/sold-permits-search), pass"
                " --url, or run"
                " 'python -m app.ingestion.houston_permits.sync --file PATH' with a"
                " downloaded report file."
            )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0), transport=self._transport, follow_redirects=True
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            raw_bytes = response.content

        suffix = pathlib.PurePosixPath(urlsplit(url).path).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            suffix = ".xlsx"  # the city publishes the weekly reports as XLSX
        retrieved_at = datetime.now(UTC)
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        storage_path = self._raw_dir / f"{retrieved_at.strftime('%Y%m%dT%H%M%S%fZ')}{suffix}"
        storage_path.write_bytes(raw_bytes)
        return RawSnapshot(
            source_name=self.source_name,
            storage_path=storage_path,
            checksum=hashlib.sha256(raw_bytes).hexdigest(),
            retrieved_at=retrieved_at,
            source_url=url,
        )

    def parse(self, snapshot: RawSnapshot) -> Iterator[dict[str, Any]]:
        """Yield permit-row dicts from a snapshot file (.xlsx or .csv export)."""
        yield from iter_report_rows(snapshot.storage_path)

    def normalize(self, record: dict[str, Any]) -> NormalizedRecord:
        """Map one report row to the canonical shape; raises ValueError when malformed."""
        return normalize_houston_permit_record(record)
