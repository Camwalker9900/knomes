"""Houston building-permits sync CLI: ``python -m app.ingestion.houston_permits.sync``.

Modes:

- ``--file PATH`` — primary mode: ingest a downloaded weekly permit activity
  report (``.xlsx`` as published, or a ``.csv`` export of the same sheet).
  The city posts one file per week on
  https://www.houstonpermittingcenter.org/sold-permits-search; historical
  weeks come from the published archive or an open-records request, ingested
  one file at a time.
- ``--url URL``   — live mode for one report: download that report file first.
- neither         — live mode using ``settings.houston_permits_source_url``
  (must point at a specific published report file; the weekly URL changes).

Either way the full pipeline (matching ladder, unmatched queue,
source_records, ledger events, sync-run metrics) is delegated to
:func:`app.ingestion.runner.run_sync`; re-running the same file creates zero
new rows.
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
from app.ingestion.houston_permits.adapter import HoustonPermitsAdapter
from app.ingestion.runner import RUN_STATUS_SUCCEEDED, run_sync

logger = logging.getLogger("app.ingestion.houston_permits.sync")


def build_snapshot_from_file(path: pathlib.Path) -> RawSnapshot:
    """Wrap a local weekly report file as a checksummed RawSnapshot."""
    resolved = path.resolve()
    raw_bytes = resolved.read_bytes()
    return RawSnapshot(
        source_name=HoustonPermitsAdapter.source_name,
        storage_path=resolved,
        checksum=hashlib.sha256(raw_bytes).hexdigest(),
        retrieved_at=datetime.now(UTC),
        source_url=None,
    )


def main(argv: list[str] | None = None) -> int:
    """Run one Houston building-permits sync; returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.houston_permits.sync",
        description="Sync City of Houston weekly permit activity reports into the ledger.",
    )
    parser.add_argument(
        "--file",
        type=pathlib.Path,
        default=None,
        metavar="PATH",
        help=(
            "local weekly report file to ingest (.xlsx as published, or a .csv export)"
            " — e.g. data/fixtures/houston_permits_sample/report.xlsx"
        ),
    )
    parser.add_argument(
        "--url",
        default=None,
        metavar="URL",
        help=(
            "download this published report file instead of using"
            " houston_permits_source_url (the weekly URL changes; find it on"
            " https://www.houstonpermittingcenter.org/sold-permits-search)"
        ),
    )
    args = parser.parse_args(argv)
    configure_logging()

    if args.file is not None and args.url is not None:
        parser.error("--file and --url are mutually exclusive")

    snapshot: RawSnapshot | None = None
    if args.file is not None:
        if not args.file.is_file():
            parser.error(f"report file not found: {args.file}")
        snapshot = build_snapshot_from_file(args.file)
    elif args.url is None and not settings.houston_permits_source_url.strip():
        parser.error(
            "no report URL configured: pass --file PATH (a downloaded weekly report),"
            " --url URL, or set houston_permits_source_url in the environment"
        )

    adapter = HoustonPermitsAdapter(source_url=args.url)
    run = run_sync(adapter, SessionLocal, snapshot=snapshot)
    logger.info(
        "houston permits sync finished",
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
