"""Normalize raw Houston code-enforcement records to the canonical ingestion shape.

Pure functions: no I/O, no database. A CKAN ``datastore_search`` record for the
Building Code Enforcement dataset carries::

    _id, project_number, address, violation_type, violation_description,
    project_status, date_opened, date_closed, last_action, last_action_date, zip

One project maps to up to three ledger event candidates, all
``GOVERNMENT_RECORD`` at confidence 1.0:

- ``CODE_VIOLATION_OPENED``   at ``date_opened`` (always; required field)
- ``CODE_VIOLATION_ACTION``   at ``last_action_date`` when ``last_action`` is
  present and the date is distinct from both ``date_opened`` and ``date_closed``
- ``CODE_VIOLATION_RESOLVED`` at ``date_closed`` when present

Malformed or missing required dates raise :class:`ValueError`; the sync runner
counts those records as rejected without aborting the run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from app.enums import EventType, VerificationLevel
from app.ingestion.base import EventCandidate, NormalizedRecord
from app.lib.address import normalize_address

RECORD_TYPE: Final[str] = "code_enforcement_project"


def _clean_str(value: Any) -> str | None:
    """Coerce a raw field to a stripped string, mapping empty/None to None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_ckan_datetime(value: Any, *, field: str) -> datetime | None:
    """Parse a CKAN ISO-8601 date/datetime field; naive values are taken as UTC.

    Returns None for absent/empty values. Raises ValueError for anything that is
    present but not ISO-8601 — the runner counts such records as rejected.
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


def _candidate(
    event_type: str, event_date: datetime, title: str, summary: str | None
) -> EventCandidate:
    return EventCandidate(
        event_type=event_type,
        event_date=event_date,
        title=title,
        summary=summary,
        verification_level=VerificationLevel.GOVERNMENT_RECORD,
        confidence=1.0,
    )


def normalize_houston_code_record(record: dict[str, Any]) -> NormalizedRecord:
    """Map one raw CKAN record to a :class:`NormalizedRecord` with event candidates."""
    source_record_id = _clean_str(record.get("project_number")) or _clean_str(record.get("_id"))
    if source_record_id is None:
        raise ValueError("record carries neither project_number nor _id")

    raw_address = _clean_str(record.get("address"))
    normalized = normalize_address(raw_address) if raw_address is not None else None

    date_opened = parse_ckan_datetime(record.get("date_opened"), field="date_opened")
    if date_opened is None:
        raise ValueError(f"record {source_record_id} has no date_opened")
    date_closed = parse_ckan_datetime(record.get("date_closed"), field="date_closed")
    last_action_date = parse_ckan_datetime(record.get("last_action_date"), field="last_action_date")

    violation_type = _clean_str(record.get("violation_type"))
    violation_description = _clean_str(record.get("violation_description"))
    last_action = _clean_str(record.get("last_action"))

    opened_title = (
        f"Code violation opened: {violation_type}"
        if violation_type is not None
        else "Code violation opened"
    )
    candidates: list[EventCandidate] = [
        _candidate(
            EventType.CODE_VIOLATION_OPENED, date_opened, opened_title, violation_description
        )
    ]
    if (
        last_action is not None
        and last_action_date is not None
        and last_action_date != date_opened
        and last_action_date != date_closed
    ):
        candidates.append(
            _candidate(
                EventType.CODE_VIOLATION_ACTION,
                last_action_date,
                f"Code enforcement action: {last_action}",
                None,
            )
        )
    if date_closed is not None:
        candidates.append(
            _candidate(
                EventType.CODE_VIOLATION_RESOLVED, date_closed, "Code violation resolved", None
            )
        )
    candidates.sort(key=lambda candidate: candidate.event_date)

    return NormalizedRecord(
        record_type=RECORD_TYPE,
        source_record_id=source_record_id,
        raw_payload=dict(record),
        normalized_address=normalized,
        hcad_account_id=None,
        event_candidates=candidates,
        raw_address=raw_address,
    )
