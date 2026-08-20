"""Houston CKAN "Project Level Actions" source adapter.

Dataset: City of Houston code-enforcement project actions on
https://data.houstontx.gov — resource ``settings.houston_actions_resource_id``
("Project Level Actions Since 2014", ~696k rows); the pre-2014 sibling
(``settings.houston_actions_pre2014_resource_id``) shares the schema and can be
pulled by passing its id as ``resource_id``.

``fetch`` pages through the CKAN ``datastore_search`` API, combines all pages,
and writes one checksummed JSON snapshot to
``data/raw/houston_actions/<timestamp>.json``. ``max_records`` bounds how many
records a live pull collects; ``filters`` is passed through as the CKAN
``datastore_search`` ``filters`` JSON parameter (e.g. ``{"Action": "Close
File"}``). ``parse`` streams the record dicts back out of a snapshot
(live-fetched or a local ``--file`` one — both use the CKAN response
envelope); when ``linked_npprjids`` is given, ``parse`` yields only records
whose ``NPPRJID`` is in that set — the storage-economy gate that keeps the
~696k mostly-unlinkable action rows out of the database while the snapshot on
disk retains the full raw pull (spec §7 reproducibility). ``normalize`` is a
thin wrapper around
:func:`app.ingestion.houston_actions.normalize.normalize_houston_action_record`.

The adapter never writes to the database; ``app.ingestion.runner.run_sync``
drives matching, source_records, ledger events, and run metrics.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections.abc import Iterator, Set
from datetime import UTC, datetime
from typing import Any, ClassVar, Final

import httpx

from app.core.config import settings
from app.ingestion.base import NormalizedRecord, RawSnapshot, SourceAdapter
from app.ingestion.houston_actions.normalize import normalize_houston_action_record
from app.ingestion.houston_code.adapter import _extract_records

SOURCE_NAME: Final[str] = "houston_code_actions"
PARSER_VERSION: Final[str] = "1.0.0"
DATASTORE_SEARCH_PATH: Final[str] = "/api/3/action/datastore_search"
DEFAULT_PAGE_SIZE: Final[int] = 1000

# .../apps/api/app/ingestion/houston_actions/adapter.py -> repo root is parents[5].
_REPO_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[5]
DEFAULT_RAW_DIR: Final[pathlib.Path] = _REPO_ROOT / "data" / "raw" / "houston_actions"


class HoustonActionsAdapter(SourceAdapter):
    """Adapter for the City of Houston "Project Level Actions" CKAN dataset."""

    source_name: ClassVar[str] = SOURCE_NAME
    parser_version: ClassVar[str] = PARSER_VERSION

    def __init__(
        self,
        *,
        raw_dir: pathlib.Path | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_records: int | None = None,
        filters: dict[str, Any] | None = None,
        resource_id: str | None = None,
        linked_npprjids: Set[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if max_records is not None and max_records < 1:
            raise ValueError("max_records must be >= 1 when given")
        self._raw_dir = raw_dir if raw_dir is not None else DEFAULT_RAW_DIR
        self._page_size = page_size
        self._max_records = max_records  # bound live pulls; None -> everything
        self._filters = filters  # CKAN datastore_search "filters" JSON param
        # None -> settings.houston_actions_resource_id at fetch time; pass
        # settings.houston_actions_pre2014_resource_id for the older sibling.
        self._resource_id = resource_id
        # Storage-economy gate: when set, parse() yields only actions whose
        # NPPRJID is in this set (NPPRJIDs of already-ingested violations).
        self._linked_npprjids = linked_npprjids
        self._transport = transport  # injectable for tests; None -> real HTTP

    async def fetch(self) -> RawSnapshot:
        """Page through datastore_search and write one combined, checksummed snapshot."""
        resource_id = (
            self._resource_id
            if self._resource_id is not None
            else settings.houston_actions_resource_id
        ).strip()
        if not resource_id:
            raise ValueError(
                "houston_actions_resource_id is not configured; set it in the environment"
                " to sync from CKAN, or run"
                " 'python -m app.ingestion.houston_actions.sync --file PATH'"
                " with a local records JSON file."
            )
        base_url = settings.houston_ckan_base_url.rstrip("/")
        url = f"{base_url}{DATASTORE_SEARCH_PATH}"

        records: list[dict[str, Any]] = []
        offset = 0
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0), transport=self._transport
        ) as client:
            while True:
                limit = self._page_size
                if self._max_records is not None:
                    limit = min(limit, self._max_records - len(records))
                # POST, not GET: CKAN accepts JSON bodies on datastore_search,
                # and large filter sets overflow a URL (414).
                body: dict[str, Any] = {
                    "resource_id": resource_id,
                    "limit": limit,
                    "offset": offset,
                }
                if self._filters:
                    body["filters"] = self._filters
                response = await client.post(url, json=body)
                response.raise_for_status()
                payload = response.json()
                if not payload.get("success", False):
                    raise RuntimeError(
                        f"CKAN datastore_search returned success=false for resource {resource_id}"
                    )
                result = payload.get("result") or {}
                page = result.get("records") or []
                records.extend(page)
                total = int(result.get("total", len(records)))
                offset += limit
                if self._max_records is not None and len(records) >= self._max_records:
                    records = records[: self._max_records]
                    break
                if not page or len(records) >= total:
                    break

        retrieved_at = datetime.now(UTC)
        combined = {"success": True, "result": {"records": records, "total": len(records)}}
        raw_bytes = json.dumps(combined, indent=2).encode("utf-8")
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        storage_path = self._raw_dir / f"{retrieved_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        storage_path.write_bytes(raw_bytes)
        return RawSnapshot(
            source_name=self.source_name,
            storage_path=storage_path,
            checksum=hashlib.sha256(raw_bytes).hexdigest(),
            retrieved_at=retrieved_at,
            source_url=f"{url}?resource_id={resource_id}",
        )

    def parse(self, snapshot: RawSnapshot) -> Iterator[dict[str, Any]]:
        """Yield raw record dicts from a snapshot file (CKAN envelope or bare list).

        With ``linked_npprjids`` set, records whose ``NPPRJID`` is not in the
        set are skipped BEFORE the runner ever sees them: the full raw pull
        stays on disk in the snapshot, but only actions linkable to an
        ingested violation project reach the database.
        """
        with open(snapshot.storage_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        for record in _extract_records(payload):
            if self._linked_npprjids is not None:
                npprjid = record.get("NPPRJID")
                key = str(npprjid).strip() if npprjid is not None else ""
                if key not in self._linked_npprjids:
                    continue
            yield record

    def normalize(self, record: dict[str, Any]) -> NormalizedRecord:
        """Map one raw record to the canonical shape; raises ValueError when malformed."""
        return normalize_houston_action_record(record)
