"""Generic source sync runner: fetch -> parse -> normalize -> match -> ledger.

``run_sync`` drives any :class:`~app.ingestion.base.SourceAdapter` end to end
and records metrics in ``source_sync_runs``. The pipeline is idempotent:

- source_records dedupe on (source_name, source_record_id, content_hash) where
  content_hash is the sha256 of the canonical JSON of the raw payload;
- record_property_matches are only inserted when no row with the same outcome
  exists for the source record (UNMATCHED rows with property_id None form the
  unmatched review queue);
- ledger events dedupe on the uq_ledger_dedup key
  (property_id, event_type, event_date, source_record_id) and are only created
  for AUTO_ACCEPTED matches — uncertain matches are never attached.

Records that raise during ``normalize`` are counted as rejected and logged;
they never abort the run. Any other failure marks the run FAILED with an
error_message, rolling back that run's record writes while preserving metrics.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import EventType, MatchReviewStatus, VerificationLevel, Visibility
from app.ingestion.base import NormalizedRecord, RawSnapshot, SourceAdapter
from app.models import LedgerEvent, RecordPropertyMatch, SourceRecord, SourceSyncRun
from app.services.matching import MatchResult, match_record_to_property

logger = logging.getLogger(__name__)

RUN_STATUS_RUNNING: Final[str] = "RUNNING"
RUN_STATUS_SUCCEEDED: Final[str] = "SUCCEEDED"
RUN_STATUS_FAILED: Final[str] = "FAILED"


def content_hash_for_payload(raw_payload: dict[str, object]) -> str:
    """SHA-256 hex digest of the canonical JSON serialization of a raw payload."""
    canonical = json.dumps(raw_payload, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_sync(
    adapter: SourceAdapter,
    session_factory: Callable[[], Session],
    *,
    snapshot: RawSnapshot | None = None,
    resolve_property: Callable[[Session, NormalizedRecord], MatchResult | None] | None = None,
) -> SourceSyncRun:
    """Run one full sync of a source adapter and return the metrics row.

    ``resolve_property``, when provided, is tried FIRST for every normalized
    record (e.g. linking a record through an upstream key rather than an
    address). If it returns a :class:`MatchResult` that result is used as-is;
    if it returns ``None`` the standard matching ladder
    (:func:`match_record_to_property`) runs as the fallback. No other behavior
    changes.
    """
    session = session_factory()
    try:
        run = SourceSyncRun(
            source_name=adapter.source_name,
            started_at=datetime.now(UTC),
            status=RUN_STATUS_RUNNING,
            parser_version=adapter.parser_version,
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

        parsed = matched = unmatched = rejected = 0
        source_records_created = events_created = 0
        snap: RawSnapshot | None = None
        error: str | None = None
        try:
            snap = snapshot if snapshot is not None else asyncio.run(adapter.fetch())
            for raw in adapter.parse(snap):
                parsed += 1
                try:
                    rec = adapter.normalize(raw)
                except Exception:
                    rejected += 1
                    logger.exception(
                        "record rejected during normalize",
                        extra={"source_name": adapter.source_name, "run_id": str(run.id)},
                    )
                    continue

                try:
                    _validate_event_candidates(rec)
                except ValueError:
                    rejected += 1
                    logger.exception(
                        "record rejected: invalid event candidate",
                        extra={"source_name": adapter.source_name, "run_id": str(run.id)},
                    )
                    continue

                result: MatchResult | None = None
                if resolve_property is not None:
                    result = resolve_property(session, rec)
                if result is None:
                    result = match_record_to_property(session, rec)
                source_record, created = _upsert_source_record(
                    session, adapter=adapter, snap=snap, rec=rec, result=result
                )
                if created:
                    source_records_created += 1
                _ensure_match_row(session, source_record_id=source_record.id, result=result)
                if result.property_id is not None:
                    matched += 1
                else:
                    unmatched += 1
                if (
                    result.review_status == MatchReviewStatus.AUTO_ACCEPTED
                    and result.property_id is not None
                ):
                    events_created += _create_ledger_events(
                        session,
                        rec=rec,
                        source_record_id=source_record.id,
                        source_name=adapter.source_name,
                        property_id=result.property_id,
                    )
        except Exception as exc:
            session.rollback()
            error = f"{type(exc).__name__}: {exc}"
            # Everything written this run was rolled back — created counters
            # must not claim rows that no longer exist.
            source_records_created = events_created = 0
            logger.exception(
                "source sync run failed",
                extra={"source_name": adapter.source_name, "run_id": str(run.id)},
            )

        run.records_parsed = parsed
        run.records_matched = matched
        run.records_unmatched = unmatched
        run.records_rejected = rejected
        run.source_records_created = source_records_created
        run.events_created = events_created
        if snap is not None:
            run.snapshot_path = str(snap.storage_path)
            run.snapshot_checksum = snap.checksum
            run.source_url = snap.source_url
        run.status = RUN_STATUS_FAILED if error is not None else RUN_STATUS_SUCCEEDED
        run.error_message = error
        run.finished_at = datetime.now(UTC)
        session.commit()
        # Fully load the row so the returned instance is usable after detach.
        session.refresh(run)
        logger.info(
            "source sync run finished",
            extra={
                "source_name": adapter.source_name,
                "run_id": str(run.id),
                "status": run.status,
                "records_parsed": parsed,
                "records_matched": matched,
                "records_unmatched": unmatched,
                "records_rejected": rejected,
                "source_records_created": source_records_created,
                "events_created": events_created,
            },
        )
        return run
    finally:
        session.close()


def _upsert_source_record(
    session: Session,
    *,
    adapter: SourceAdapter,
    snap: RawSnapshot,
    rec: NormalizedRecord,
    result: MatchResult,
) -> tuple[SourceRecord, bool]:
    """Insert the source record unless the identical content already exists.

    Returns the (possibly pre-existing) row and whether it was newly created.
    """
    content_hash = content_hash_for_payload(rec.raw_payload)
    existing = session.execute(
        select(SourceRecord)
        .where(
            SourceRecord.source_name == adapter.source_name,
            SourceRecord.source_record_id == rec.source_record_id,
            SourceRecord.content_hash == content_hash,
        )
        .order_by(SourceRecord.created_at)
        .limit(1)
    ).scalar_one_or_none()
    accepted_property_id = (
        result.property_id
        if result.review_status == MatchReviewStatus.AUTO_ACCEPTED
        else None
    )
    if existing is not None:
        if accepted_property_id is not None and existing.property_id is None:
            existing.property_id = accepted_property_id
        return existing, False

    source_record = SourceRecord(
        source_name=adapter.source_name,
        source_record_id=rec.source_record_id,
        property_id=accepted_property_id,
        record_type=rec.record_type,
        raw_payload=rec.raw_payload,
        source_url=snap.source_url,
        retrieved_at=snap.retrieved_at,
        content_hash=content_hash,
        parser_version=adapter.parser_version,
    )
    session.add(source_record)
    session.flush()
    return source_record, True


def _ensure_match_row(
    session: Session,
    *,
    source_record_id: uuid.UUID,
    result: MatchResult,
) -> None:
    """Record the match outcome once; identical re-runs insert nothing new.

    UNMATCHED rows keep property_id None — they are the unmatched review queue.
    """
    exists = session.execute(
        select(RecordPropertyMatch.id)
        .where(
            RecordPropertyMatch.source_record_id == source_record_id,
            RecordPropertyMatch.property_id == result.property_id,
            RecordPropertyMatch.match_method == result.method,
            RecordPropertyMatch.review_status == result.review_status,
        )
        .limit(1)
    ).first()
    if exists is not None:
        return
    session.add(
        RecordPropertyMatch(
            source_record_id=source_record_id,
            property_id=result.property_id,
            match_method=result.method,
            confidence=result.confidence,
            review_status=result.review_status,
            match_reason=result.reason,
        )
    )
    session.flush()


def _validate_event_candidates(rec: NormalizedRecord) -> None:
    """Reject candidates whose enum-valued fields are not real enum members.

    Adapters are trusted code, but a typo here would otherwise flow straight
    onto the public ledger as an unfilterable event type or a fabricated
    verification level.
    """
    for candidate in rec.event_candidates:
        if candidate.event_type not in set(EventType):
            raise ValueError(f"unknown event_type {candidate.event_type!r}")
        if candidate.verification_level not in set(VerificationLevel):
            raise ValueError(f"unknown verification_level {candidate.verification_level!r}")


def _create_ledger_events(
    session: Session,
    *,
    rec: NormalizedRecord,
    source_record_id: uuid.UUID,
    source_name: str,
    property_id: uuid.UUID,
) -> int:
    """Materialize event candidates, deduplicating on LOGICAL record identity.

    A refreshed upstream payload creates a new content-versioned source_records
    row (new UUID), so deduplicating on the FK alone would re-materialize every
    candidate each time the upstream record mutates (e.g. a CKAN project whose
    last_action changes while date_opened stays fixed). The dedup key is
    therefore (property, event_type, event_date) against ANY prior version of
    the same upstream record — (source_name, source_record_id) across
    source_records rows. uq_ledger_dedup remains as a same-version backstop.
    """
    created = 0
    for candidate in rec.event_candidates:
        exists = session.execute(
            select(LedgerEvent.id)
            .join(SourceRecord, LedgerEvent.source_record_id == SourceRecord.id)
            .where(
                LedgerEvent.property_id == property_id,
                LedgerEvent.event_type == candidate.event_type,
                LedgerEvent.event_date == candidate.event_date,
                SourceRecord.source_name == source_name,
                SourceRecord.source_record_id == rec.source_record_id,
            )
            .limit(1)
        ).first()
        if exists is not None:
            continue
        session.add(
            LedgerEvent(
                property_id=property_id,
                event_type=candidate.event_type,
                event_date=candidate.event_date,
                title=candidate.title,
                summary=candidate.summary,
                source_record_id=source_record_id,
                verification_level=candidate.verification_level,
                confidence=candidate.confidence,
                visibility=Visibility.PUBLIC,
            )
        )
        created += 1
    if created:
        session.flush()
    return created
