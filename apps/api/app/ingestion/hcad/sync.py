"""HCAD real_acct sync CLI — the canonical property bootstrap pipeline.

Usage::

    python -m app.ingestion.hcad.sync --file PATH   # local export (fixture or sample)
    python -m app.ingestion.hcad.sync               # uses settings.hcad_download_url
    python -m app.ingestion.hcad.sync --url URL     # explicit download URL

Pipeline: build a checksummed :class:`RawSnapshot` -> archive the snapshot
file under ``data/raw/hcad/<timestamp>_<checksum8>.txt`` -> COPY into the
UNLOGGED ``hcad_staging`` table -> set-based property upsert keyed on
``hcad_account_id`` (+ ``property_addresses`` aliases) -> per-row raw
``source_records`` deduped on content_hash and linked to their properties.
HCAD bootstraps properties; it emits **no** ledger events in this phase.

Counter mapping onto ``SourceSyncRun`` fields (the model has no dedicated
inserted/updated columns, so the pipeline's counts map as follows):

- ``records_parsed``   <- data rows read from the snapshot (valid + malformed)
- ``records_rejected`` <- malformed rows skipped by the parser + rows failing
  field validation (garbage/negative numerics, missing street parts)
- ``records_matched``  <- valid rows applied to a property (each valid row
  maps to its property by hcad_account_id — the bootstrap "match")
- ``records_unmatched``<- always 0 (the bootstrap creates missing properties)
- ``source_records_created`` <- new raw source_records rows (content-hash dedup)
- ``events_created``   <- always 0 (property bootstrap emits no ledger events)

The properties inserted/updated/unchanged split and the alias count appear in
the structured log lines only.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.ingestion.base import RawSnapshot
from app.ingestion.hcad.download import (
    DEFAULT_RAW_DIR,
    fetch_snapshot,
    snapshot_from_file,
)
from app.ingestion.hcad.load import (
    UpsertStats,
    attach_source_record_properties,
    insert_source_records,
    load_staging,
    upsert_from_staging,
)
from app.ingestion.hcad.normalize import HCAD_PARSER_VERSION, HCAD_SOURCE_NAME
from app.ingestion.hcad.parse import ParseStats
from app.ingestion.runner import (
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCEEDED,
)
from app.models import SourceSyncRun

logger = logging.getLogger(__name__)


def _archive_snapshot(snapshot: RawSnapshot, raw_dir: Path) -> RawSnapshot:
    """Copy the snapshot file into data/raw/hcad/<timestamp>_<checksum8>.txt."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = snapshot.retrieved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = raw_dir / f"{stamp}_{snapshot.checksum[:8]}.txt"
    if target.resolve() != snapshot.storage_path.resolve():
        shutil.copy2(snapshot.storage_path, target)
    logger.info(
        "hcad snapshot archived",
        extra={
            "source_name": HCAD_SOURCE_NAME,
            "storage_path": str(target),
            "checksum": snapshot.checksum,
        },
    )
    return replace(snapshot, storage_path=target)


def run_hcad_sync(
    session_factory: Callable[[], Session],
    *,
    file: Path | None = None,
    url: str | None = None,
    raw_dir: Path | None = None,
) -> SourceSyncRun:
    """Run one full HCAD sync (staging COPY -> upsert -> aliases -> provenance).

    Returns the persisted :class:`SourceSyncRun` metrics row. Pipeline errors
    mark the run FAILED with an ``error_message`` instead of raising; only
    snapshot acquisition problems (no URL/file) raise.
    """
    archive_dir = raw_dir if raw_dir is not None else DEFAULT_RAW_DIR
    if file is not None:
        snapshot = snapshot_from_file(file)
    else:
        snapshot = fetch_snapshot(
            url or settings.hcad_download_url or None, archive_dir / "downloads"
        )
    snapshot = _archive_snapshot(snapshot, archive_dir)

    session = session_factory()
    try:
        run = SourceSyncRun(
            source_name=HCAD_SOURCE_NAME,
            started_at=datetime.now(UTC),
            status=RUN_STATUS_RUNNING,
            parser_version=HCAD_PARSER_VERSION,
            records_parsed=0,
            records_matched=0,
            records_unmatched=0,
            records_rejected=0,
            source_records_created=0,
            events_created=0,
        )
        session.add(run)
        # Commit immediately so the run row survives a rollback on failure.
        session.commit()

        parse_stats = ParseStats()
        upsert_stats = UpsertStats()
        source_records_created = 0
        error: str | None = None
        try:
            parse_stats = load_staging(session.connection(), snapshot.storage_path)
            upsert_stats = upsert_from_staging(session)
            source_records_created = insert_source_records(session, snapshot)
            linked = attach_source_record_properties(session)
            logger.info(
                "hcad source records linked",
                extra={"source_name": HCAD_SOURCE_NAME, "records_linked": linked},
            )
        except Exception as exc:
            session.rollback()
            error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "hcad sync failed",
                extra={"source_name": HCAD_SOURCE_NAME, "run_id": str(run.id)},
            )

        run.records_parsed = parse_stats.rows_read
        run.records_rejected = parse_stats.rows_skipped + upsert_stats.rows_rejected
        run.records_matched = upsert_stats.rows_valid
        run.records_unmatched = 0
        run.source_records_created = source_records_created
        run.events_created = 0
        run.snapshot_path = str(snapshot.storage_path)
        run.snapshot_checksum = snapshot.checksum
        run.source_url = snapshot.source_url
        run.status = RUN_STATUS_FAILED if error is not None else RUN_STATUS_SUCCEEDED
        run.error_message = error
        run.finished_at = datetime.now(UTC)
        session.commit()
        session.refresh(run)
        logger.info(
            "hcad sync finished",
            extra={
                "source_name": HCAD_SOURCE_NAME,
                "run_id": str(run.id),
                "status": run.status,
                "records_parsed": run.records_parsed,
                "records_matched": run.records_matched,
                "records_rejected": run.records_rejected,
                "source_records_created": run.source_records_created,
                "properties_inserted": upsert_stats.properties_inserted,
                "properties_updated": upsert_stats.properties_updated,
                "properties_unchanged": upsert_stats.properties_unchanged,
                "aliases_inserted": upsert_stats.aliases_inserted,
                "snapshot_checksum": snapshot.checksum,
            },
        )
        return run
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``python -m app.ingestion.hcad.sync [--file PATH]``."""
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.hcad.sync",
        description="Import an HCAD real_acct export (canonical property bootstrap).",
    )
    parser.add_argument(
        "--file", type=Path, default=None, help="local real_acct.txt export to import"
    )
    parser.add_argument(
        "--url",
        default=None,
        help="download URL (default: settings.hcad_download_url)",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=None,
        help="snapshot archive directory (default: <repo>/data/raw/hcad)",
    )
    args = parser.parse_args(argv)
    configure_logging()
    try:
        run = run_hcad_sync(
            SessionLocal, file=args.file, url=args.url, raw_dir=args.raw_dir
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"hcad sync {run.status}: parsed={run.records_parsed}"
        f" matched={run.records_matched} rejected={run.records_rejected}"
        f" source_records_created={run.source_records_created}"
    )
    return 0 if run.status == RUN_STATUS_SUCCEEDED else 1


if __name__ == "__main__":
    raise SystemExit(main())
