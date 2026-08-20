"""Normalize raw Houston project-level action records to the canonical shape.

Pure functions: no I/O, no database. A CKAN ``datastore_search`` record from
the "Project Level Actions Since 2014" resource (settings
``houston_actions_resource_id``; the pre-2014 sibling shares the schema)
carries::

    _id, NPPRJID, Sr_Request_Num, ViolationSubId, Action_Level, Action_Type,
    Action_Id, Action_Date, Action, Comments

Shape verified live against data.houstontx.gov on 2026-08-19 (``Action_Id``
values like ``"PA371451"``, ``Action_Date`` like ``"2014-01-07"``).

One action row maps to at most one ledger event candidate:

- ``Action == "Close File"``     -> ``CODE_VIOLATION_RESOLVED`` at
  ``Action_Date``, title "Code violation case closed";
- any other non-empty ``Action`` -> ``CODE_VIOLATION_ACTION`` at
  ``Action_Date``, title "Code enforcement action: {Action}";
- empty/missing ``Action``       -> no event candidate (the record is still
  retained as provenance).

Both candidates carry ``GOVERNMENT_RECORD`` at confidence 1.0. ``Action`` is a
system code-list value (e.g. "Close File", "Send Violation Letter") and safe
for public titles. ``Comments`` is inspector free text that can contain
personal names (owner/tenant PII); it is retained only inside ``raw_payload``
(private provenance) and must NEVER be used in a public event title or summary.

Action records carry NO address and NO HCAD account — the only linkage to a
property is ``NPPRJID``, resolved against previously ingested
``houston_code_enforcement`` violations (see ``sync.resolve_action_property``).

A malformed ``Action_Date``, or a missing one when the row names an ``Action``,
raises :class:`ValueError`; the sync runner counts those records as rejected
without aborting the run.
"""

from __future__ import annotations

from typing import Any, Final

from app.enums import EventType, VerificationLevel
from app.ingestion.base import EventCandidate, NormalizedRecord
from app.ingestion.houston_code.normalize import parse_ckan_datetime

RECORD_TYPE: Final[str] = "code_enforcement_action"
CLOSE_FILE_ACTION: Final[str] = "close file"  # casefolded comparison key
RESOLVED_TITLE: Final[str] = "Code violation case closed"


def _clean_str(value: Any) -> str | None:
    """Coerce a raw field to a stripped string, mapping empty/None to None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_houston_action_record(record: dict[str, Any]) -> NormalizedRecord:
    """Map one raw CKAN action record to a :class:`NormalizedRecord`."""
    source_record_id = _clean_str(record.get("Action_Id"))
    if source_record_id is None:
        npprjid = _clean_str(record.get("NPPRJID"))
        ckan_id = _clean_str(record.get("_id"))
        if npprjid is not None and ckan_id is not None:
            source_record_id = f"{npprjid}-{ckan_id}"
    if source_record_id is None:
        raise ValueError("record carries neither Action_Id nor an NPPRJID/_id pair")

    # Malformed dates raise here regardless of whether an event is emitted —
    # a record whose only fact is undated is not a usable government record.
    action_date = parse_ckan_datetime(record.get("Action_Date"), field="Action_Date")
    action = _clean_str(record.get("Action"))

    candidates: list[EventCandidate] = []
    if action is not None:
        if action_date is None:
            raise ValueError(f"record {source_record_id} has Action {action!r} but no Action_Date")
        if action.casefold() == CLOSE_FILE_ACTION:
            event_type = EventType.CODE_VIOLATION_RESOLVED
            title = RESOLVED_TITLE
        else:
            event_type = EventType.CODE_VIOLATION_ACTION
            title = f"Code enforcement action: {action}"
        # Comments is free text that may contain names and must never appear
        # in the title or summary; there is nothing else safe to summarize.
        candidates.append(
            EventCandidate(
                event_type=event_type,
                event_date=action_date,
                title=title,
                summary=None,
                verification_level=VerificationLevel.GOVERNMENT_RECORD,
                confidence=1.0,
            )
        )

    # No address and no HCAD account exist on this resource: property linkage
    # happens exclusively through NPPRJID via the resolve_property hook.
    return NormalizedRecord(
        record_type=RECORD_TYPE,
        source_record_id=source_record_id,
        raw_payload=dict(record),
        normalized_address=None,
        hcad_account_id=None,
        event_candidates=candidates,
        raw_address=None,
    )
