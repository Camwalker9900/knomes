"""Houston project-actions sync CLI: ``python -m app.ingestion.houston_actions.sync``.

Modes:

- ``--file PATH`` — fixture/manual mode: wrap a local CKAN-shaped records JSON
  file as a checksummed :class:`RawSnapshot` and run the pipeline against it.
- no ``--file``   — live mode: the adapter pages through the CKAN
  ``datastore_search`` API (requires ``houston_actions_resource_id`` in
  settings). ``--max-records N`` bounds how many records are pulled and
  ``--pre-2014`` targets the pre-2014 sibling resource
  (``houston_actions_pre2014_resource_id``) — live-mode options cannot combine
  with ``--file``.

Two things distinguish this source from ``houston_code``:

1. **Storage economy.** The resource holds ~696k rows, action records carry no
   address and no HCAD account, and most belong to projects we never ingested
   — flooding source_records/the unmatched queue with them would be pure
   noise. Before the runner sees anything, the set of known ``NPPRJID`` values
   is loaded from ingested ``houston_code_enforcement`` violations and the
   adapter's ``parse`` drops every action outside that set. The raw snapshot
   on disk retains the FULL pull for spec §7 reproducibility; only linkable
   records reach the database.
2. **NPPRJID property resolution.** :func:`resolve_action_property` is passed
   to :func:`run_sync` as the ``resolve_property`` hook: it joins the action's
   ``NPPRJID`` to already-ingested violations and reuses their AUTO_ACCEPTED
   property match. Actions on a known project whose violation is still
   unmatched land in the review queue (UNMATCHED, no events) — never silently
   attached.

Everything else (source_records dedup, ledger events, sync-run metrics) is
delegated to :func:`app.ingestion.runner.run_sync`.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import pathlib
import sys
from datetime import UTC, datetime

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.enums import MatchMethod, MatchReviewStatus
from app.ingestion.base import NormalizedRecord, RawSnapshot
from app.ingestion.houston_actions.adapter import HoustonActionsAdapter
from app.ingestion.runner import RUN_STATUS_SUCCEEDED, run_sync
from app.models import RecordPropertyMatch, SourceRecord
from app.services.matching import MatchResult

logger = logging.getLogger("app.ingestion.houston_actions.sync")

VIOLATIONS_SOURCE_NAME = "houston_code_enforcement"


def load_linked_npprjids(session: Session) -> set[str]:
    """NPPRJIDs of every ingested code-enforcement violation (the linkable set)."""
    npprjid = SourceRecord.raw_payload["NPPRJID"].astext
    rows = session.execute(
        select(distinct(npprjid)).where(
            SourceRecord.source_name == VIOLATIONS_SOURCE_NAME,
            npprjid.is_not(None),
        )
    ).scalars()
    return {value.strip() for value in rows if value is not None and value.strip()}


def resolve_action_property(session: Session, rec: NormalizedRecord) -> MatchResult | None:
    """Resolve an action to a property through its violation project (NPPRJID).

    Action records carry no address and no HCAD account, so the standard
    matching ladder can never place them. The ONLY linkage is ``NPPRJID``:
    ingested ``houston_code_enforcement`` violations keep it in raw_payload,
    and their AUTO_ACCEPTED property match is reused here (MANUAL method,
    confidence 1.0 — the join is deterministic on the government's own key).

    No linked project (or no auto-accepted violation match) -> UNMATCHED with
    property_id None: the action joins the review queue and creates no events.
    """
    npprjid_raw = rec.raw_payload.get("NPPRJID")
    npprjid = str(npprjid_raw).strip() if npprjid_raw is not None else ""
    if not npprjid:
        return MatchResult(
            property_id=None,
            method=None,
            confidence=0.0,
            reason="action record carries no NPPRJID to link through",
            review_status=MatchReviewStatus.UNMATCHED,
        )

    property_id = session.execute(
        select(RecordPropertyMatch.property_id)
        .join(SourceRecord, RecordPropertyMatch.source_record_id == SourceRecord.id)
        .where(
            SourceRecord.source_name == VIOLATIONS_SOURCE_NAME,
            SourceRecord.raw_payload["NPPRJID"].astext == npprjid,
            RecordPropertyMatch.review_status == MatchReviewStatus.AUTO_ACCEPTED,
            RecordPropertyMatch.property_id.is_not(None),
        )
        .order_by(RecordPropertyMatch.created_at, RecordPropertyMatch.id)
        .limit(1)
    ).scalar_one_or_none()
    if property_id is None:
        return MatchResult(
            property_id=None,
            method=None,
            confidence=0.0,
            reason=(
                f"no auto-accepted violation match for code-enforcement project NPPRJID {npprjid}"
            ),
            review_status=MatchReviewStatus.UNMATCHED,
        )
    return MatchResult(
        property_id=property_id,
        method=MatchMethod.MANUAL,
        confidence=1.0,
        reason=f"linked via code-enforcement project NPPRJID {npprjid}",
        review_status=MatchReviewStatus.AUTO_ACCEPTED,
    )


def build_snapshot_from_file(path: pathlib.Path) -> RawSnapshot:
    """Wrap a local CKAN-shaped records JSON file as a checksummed RawSnapshot."""
    resolved = path.resolve()
    raw_bytes = resolved.read_bytes()
    return RawSnapshot(
        source_name=HoustonActionsAdapter.source_name,
        storage_path=resolved,
        checksum=hashlib.sha256(raw_bytes).hexdigest(),
        retrieved_at=datetime.now(UTC),
        source_url=None,
    )


def _known_project_ids() -> set[str]:
    """Load the linkable NPPRJID set through a short-lived session."""
    with SessionLocal() as session:
        return load_linked_npprjids(session)


def main(argv: list[str] | None = None) -> int:
    """Run one Houston project-actions sync; returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.houston_actions.sync",
        description="Sync Houston code-enforcement project actions into the ledger.",
    )
    parser.add_argument(
        "--file",
        type=pathlib.Path,
        default=None,
        metavar="PATH",
        help=(
            "local CKAN-shaped records JSON file to ingest instead of fetching live"
            " (e.g. data/fixtures/houston_actions_sample/records.json)"
        ),
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        metavar="N",
        help="live mode only: stop paging once N records have been collected",
    )
    parser.add_argument(
        "--pre-2014",
        dest="pre_2014",
        action="store_true",
        help="live mode only: pull the pre-2014 sibling resource instead",
    )
    args = parser.parse_args(argv)
    configure_logging()

    if args.max_records is not None and args.max_records < 1:
        parser.error("--max-records must be >= 1")

    snapshot: RawSnapshot | None = None
    if args.file is not None:
        if args.max_records is not None or args.pre_2014:
            parser.error("--max-records/--pre-2014 apply to live CKAN mode; not valid with --file")
        if not args.file.is_file():
            parser.error(f"snapshot file not found: {args.file}")
        snapshot = build_snapshot_from_file(args.file)
    elif args.pre_2014 and not settings.houston_actions_pre2014_resource_id.strip():
        parser.error(
            "houston_actions_pre2014_resource_id is not configured;"
            " set it in the environment for live CKAN sync, or pass --file PATH"
        )
    elif not args.pre_2014 and not settings.houston_actions_resource_id.strip():
        parser.error(
            "houston_actions_resource_id is not configured;"
            " set it in the environment for live CKAN sync, or pass --file PATH"
        )

    # Storage economy: only actions on already-ingested violation projects may
    # reach the runner — ~696k rows with no address must not flood the queue.
    linked_npprjids = _known_project_ids()
    logger.info(
        "loaded linkable violation projects",
        extra={"linked_npprjid_count": len(linked_npprjids)},
    )

    adapter = HoustonActionsAdapter(
        max_records=args.max_records,
        resource_id=(settings.houston_actions_pre2014_resource_id if args.pre_2014 else None),
        linked_npprjids=linked_npprjids,
    )
    run = run_sync(
        adapter, SessionLocal, snapshot=snapshot, resolve_property=resolve_action_property
    )
    logger.info(
        "houston actions sync finished",
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
