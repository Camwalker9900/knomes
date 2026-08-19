"""Normalize raw Houston code-enforcement records to the canonical ingestion shape.

Pure functions: no I/O, no database. A CKAN ``datastore_search`` record from
resource ``1446a3ec-2633-4cf1-b15d-6dae9a07c4ed`` ("All Code Enforcement
Violations in FORMS Since 2014") carries::

    _id, NPPRJID, Sr_Request_Num, RecordCreateDate, HCAD, Merged_Situs, Space,
    Zip, CouncilDistrict, Subdivision, LegalDescription, "Legal 2",
    Received_Method, Project_Status, Comment311upd, Violation_Category,
    ViolationSubId, Ordno, ShortDescription, DeadLineDate, CheckBackDate

One violation row maps to exactly one ledger event candidate:

- ``CODE_VIOLATION_OPENED`` at ``RecordCreateDate`` (``GOVERNMENT_RECORD``,
  confidence 1.0). The summary is ``ShortDescription`` (published ordinance
  text) truncated to 500 characters.

There is **no** ``CODE_VIOLATION_RESOLVED`` candidate: the resource carries no
closure date (``DeadLineDate`` is a compliance deadline and ``CheckBackDate``
a re-inspection date — neither is when the violation was resolved), and a
``Project_Status`` of ``CLOSED`` without a date must not become a manufactured
event. ``Project_Status`` stays visible in ``raw_payload`` for provenance.

``Comment311upd`` is inspector free text that can contain personal names. It is
retained only inside ``raw_payload`` (private provenance) and must NEVER be
used in a public event title or summary.

``HCAD`` is the Harris County appraisal account number; when present it feeds
``hcad_account_id`` so the matching ladder's HCAD_ID rung fires at 1.0.

Malformed or missing ``RecordCreateDate`` raises :class:`ValueError`; the sync
runner counts those records as rejected without aborting the run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from app.enums import EventType, VerificationLevel
from app.ingestion.base import EventCandidate, NormalizedRecord
from app.lib.address import normalize_address

RECORD_TYPE: Final[str] = "code_enforcement_violation"
SUMMARY_MAX_CHARS: Final[int] = 500


def _clean_str(value: Any) -> str | None:
    """Coerce a raw field to a stripped string, mapping empty/None to None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_ckan_datetime(value: Any, *, field: str) -> datetime | None:
    """Parse a CKAN date/datetime field; naive values are taken as UTC.

    Accepts ``YYYY-MM-DD HH:MM:SS`` (the dataset's native format) and ISO-8601
    variants — both are handled by :meth:`datetime.fromisoformat`. Returns None
    for absent/empty values. Raises ValueError for anything that is present but
    unparseable — the runner counts such records as rejected.
    """
    text = _clean_str(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"malformed {field}: {text!r} is not an ISO-8601 date") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def normalize_houston_code_record(record: dict[str, Any]) -> NormalizedRecord:
    """Map one raw CKAN violation record to a :class:`NormalizedRecord`."""
    source_record_id = _clean_str(record.get("ViolationSubId"))
    if source_record_id is None:
        npprjid = _clean_str(record.get("NPPRJID"))
        ckan_id = _clean_str(record.get("_id"))
        if npprjid is not None and ckan_id is not None:
            source_record_id = f"{npprjid}-{ckan_id}"
    if source_record_id is None:
        raise ValueError("record carries neither ViolationSubId nor an NPPRJID/_id pair")

    hcad_account_id = _clean_str(record.get("HCAD"))
    raw_address = _clean_str(record.get("Merged_Situs"))
    normalized = normalize_address(raw_address) if raw_address is not None else None

    record_create_date = parse_ckan_datetime(
        record.get("RecordCreateDate"), field="RecordCreateDate"
    )
    if record_create_date is None:
        raise ValueError(f"record {source_record_id} has no RecordCreateDate")

    violation_category = _clean_str(record.get("Violation_Category"))
    short_description = _clean_str(record.get("ShortDescription"))

    title = (
        f"Code violation opened: {violation_category}"
        if violation_category is not None
        else "Code violation opened"
    )
    # ShortDescription is published ordinance text — safe for the public
    # ledger. Comment311upd is free text that may contain names and must never
    # appear here.
    summary = short_description[:SUMMARY_MAX_CHARS] if short_description is not None else None

    candidates = [
        EventCandidate(
            event_type=EventType.CODE_VIOLATION_OPENED,
            event_date=record_create_date,
            title=title,
            summary=summary,
            verification_level=VerificationLevel.GOVERNMENT_RECORD,
            confidence=1.0,
        )
    ]
    # No CODE_VIOLATION_RESOLVED: this resource has no closure date, and a
    # CLOSED Project_Status without a date would be a manufactured fact.

    return NormalizedRecord(
        record_type=RECORD_TYPE,
        source_record_id=source_record_id,
        raw_payload=dict(record),
        normalized_address=normalized,
        hcad_account_id=hcad_account_id,
        event_candidates=candidates,
        raw_address=raw_address,
    )
