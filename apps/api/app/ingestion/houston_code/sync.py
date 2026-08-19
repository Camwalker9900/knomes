"""Houston code-enforcement sync CLI: ``python -m app.ingestion.houston_code.sync``.

Modes:

- ``--file PATH`` — fixture/manual mode: wrap a local CKAN-shaped records JSON
  file as a checksummed :class:`RawSnapshot` and run the pipeline against it.
- no arguments   — live mode: the adapter pages through the CKAN
  ``datastore_search`` API (requires ``houston_code_resource_id`` in settings).

Either way the full pipeline (matching ladder, unmatched queue, source_records,
ledger events, sync-run metrics) is delegated to
:func:`app.ingestion.runner.run_sync`.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import pathlib
import sys
from datetime import UTC, datetime

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.ingestion.base import RawSnapshot
from app.ingestion.houston_code.adapter import HoustonCodeAdapter
from app.ingestion.runner import RUN_STATUS_SUCCEEDED, run_sync

logger = logging.getLogger("app.ingestion.houston_code.sync")


def build_snapshot_from_file(path: pathlib.Path) -> RawSnapshot:
    """Wrap a local CKAN-shaped records JSON file as a checksummed RawSnapshot."""
    resolved = path.resolve()
    raw_bytes = resolved.read_bytes()
    return RawSnapshot(
        source_name=HoustonCodeAdapter.source_name,
        storage_path=resolved,
        checksum=hashlib.sha256(raw_bytes).hexdigest(),
        retrieved_at=datetime.now(UTC),
        source_url=None,
    )


def main(argv: list[str] | None = None) -> int:
    """Run one Houston code-enforcement sync; returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.houston_code.sync",
        description="Sync Houston Building Code Enforcement records into the ledger.",
    )
    parser.add_argument(
        "--file",
        type=pathlib.Path,
        default=None,
        metavar="PATH",
        help=(
            "local CKAN-shaped records JSON file to ingest instead of fetching live"
            " (e.g. data/fixtures/houston_code_sample/records.json)"
        ),
    )
    args = parser.parse_args(argv)
    configure_logging()

    snapshot: RawSnapshot | None = None
    if args.file is not None:
        if not args.file.is_file():
            parser.error(f"snapshot file not found: {args.file}")
        snapshot = build_snapshot_from_file(args.file)
    elif not settings.houston_code_resource_id.strip():
        parser.error(
            "houston_code_resource_id is not configured;"
            " set it in the environment for live CKAN sync, or pass --file PATH"
        )

    run = run_sync(HoustonCodeAdapter(), SessionLocal, snapshot=snapshot)
    logger.info(
        "houston code sync finished",
        extra={
            "run_id": str(run.id),
            "status": run.status,
            "mode": "file" if args.file is not None else "live",
            "records_parsed": run.records_parsed,
            "records_matched": run.records_matched,
            "records_unmatched": run.records_unmatched,
            "records_rejected": run.records_rejected,
            "source_records_created": run.source_records_created,
            "events_created": run.events_created,
            "snapshot_path": run.snapshot_path,
            "error_message": run.error_message,
        },
    )
    return 0 if run.status == RUN_STATUS_SUCCEEDED else 1


if __name__ == "__main__":
    sys.exit(main())
