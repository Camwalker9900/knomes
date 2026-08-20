"""Normalize Houston weekly permit-report rows to the canonical ingestion shape.

Pure functions: no I/O, no database. One row of the Houston Permitting Center
weekly permit activity report (see ``parse.py`` for the verified layout)
carries::

    Zip Code | Permit Date | Permit Type | Project No | Address | Comments

and maps to exactly one ledger event candidate:

- ``PERMIT_ISSUED`` at ``Permit Date`` (``GOVERNMENT_RECORD``, confidence 1.0).
  The report lists permits *sold* (issued) during the reporting week, so
  ``Permit Date`` is the issue date. The format carries **no** application or
  finalization dates, so no ``PERMIT_APPLIED``/``PERMIT_FINALIZED`` candidate
  is ever produced — that would be a manufactured fact.

``Comments`` is applicant-provided free text; the report's own footer says the
City "does not confirm or verify" it, and it can contain personal names (e.g.
"<SURNAME> RESIDENCE REMODEL"). It is retained only inside ``raw_payload``
(private provenance) and must NEVER be used in a public event title or summary.

``Project No`` is the city permit/project number — the stable
``source_record_id`` for idempotent re-imports across weekly files. It appears
in the public summary only when it matches a strict digits-first pattern, so
free text can never leak through that path.

The report has no HCAD account column, so ``hcad_account_id`` is always None
and matching relies on the EXACT_ADDRESS / alias / fuzzy ladder rungs.

Missing/malformed ``Permit Date`` or a missing ``Project No`` raises
:class:`ValueError`; the sync runner counts those records as rejected without
aborting the run.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.enums import EventType, VerificationLevel
from app.ingestion.base import EventCandidate, NormalizedRecord
from app.ingestion.houston_permits.parse import (
    ADDRESS_COLUMN,
    DATE_COLUMN,
    NUMBER_COLUMN,
    TYPE_COLUMN,
)
from app.lib.address import normalize_address

RECORD_TYPE: Final[str] = "building_permit"

# Display labels for the permit-type codes observed in real reports. Unknown
# codes pass through verbatim — never invent a meaning for a code we have not
# seen documented.
PERMIT_TYPE_LABELS: Final[dict[str, str]] = {
    "BUILDING PMT": "Building permit",
    "DEMOLITION": "Demolition permit",
    "OCC-BLDG PMT": "Occupancy building permit",
}

# Permit numbers are digit sequences in the published reports (e.g. 26030001).
# Only a value shaped like this may appear in a public summary.
_SAFE_PERMIT_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"\d[\dA-Z-]{0,19}")

# Plausible Excel serial-date window (~1954 .. ~2064): a producer change that
# stores Permit Date as a number must not silently reject rows.
_EXCEL_SERIAL_EPOCH: Final[datetime] = datetime(1899, 12, 30, tzinfo=UTC)
_EXCEL_SERIAL_MIN: Final[int] = 20_000
_EXCEL_SERIAL_MAX: Final[int] = 60_000


def _clean_str(value: Any) -> str | None:
    """Coerce a raw field to a stripped string, mapping empty/None to None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _strip_excel_float(text: str) -> str:
    """Drop a spurious trailing '.0' that numeric cells gain in some exports."""
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def parse_permit_date(value: Any, *, field: str = DATE_COLUMN) -> datetime | None:
    """Parse a report date; naive values are taken as UTC midnight.

    Accepts the report's native ``YYYY/MM/DD``, ISO-8601 variants, and an
    Excel serial number (in case a future export stores the column as a
    number). Returns None for absent/empty values. Raises ValueError for
    anything present but unparseable — the runner counts those as rejected.
    """
    text = _clean_str(value)
    if text is None:
        return None
    for fmt in ("%Y/%m/%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        serial_text = _strip_excel_float(text)
        if serial_text.isdigit() and _EXCEL_SERIAL_MIN <= int(serial_text) <= _EXCEL_SERIAL_MAX:
            return _EXCEL_SERIAL_EPOCH + timedelta(days=int(serial_text))
        raise ValueError(f"malformed {field}: {text!r} is not a recognizable date") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def permit_title(permit_type: str | None) -> str:
    """Public event title from the permit-type code (never from Comments)."""
    if permit_type is None:
        return "Permit issued"
    label = PERMIT_TYPE_LABELS.get(permit_type.upper(), permit_type)
    return f"Permit issued: {label}"


def permit_summary(project_no: str, permit_type: str | None) -> str:
    """Public event summary: permit number + type code only — no free text."""
    if _SAFE_PERMIT_NUMBER_RE.fullmatch(project_no.upper()) is None:
        # Unexpectedly shaped identifier: keep it out of the public summary.
        return "City of Houston weekly permit activity report."
    suffix = f" ({permit_type})" if permit_type is not None else ""
    return f"Permit no. {project_no}{suffix} — City of Houston weekly permit activity report."


def normalize_houston_permit_record(record: dict[str, Any]) -> NormalizedRecord:
    """Map one weekly-report row to a :class:`NormalizedRecord`."""
    project_no_raw = _clean_str(record.get(NUMBER_COLUMN))
    if project_no_raw is None:
        raise ValueError("record carries no Project No (permit number)")
    project_no = _strip_excel_float(project_no_raw)

    permit_date = parse_permit_date(record.get(DATE_COLUMN))
    if permit_date is None:
        raise ValueError(f"record {project_no} has no {DATE_COLUMN}")

    permit_type = _clean_str(record.get(TYPE_COLUMN))
    raw_address = _clean_str(record.get(ADDRESS_COLUMN))
    normalized = normalize_address(raw_address) if raw_address is not None else None

    candidates = [
        EventCandidate(
            event_type=EventType.PERMIT_ISSUED,
            event_date=permit_date,
            title=permit_title(permit_type),
            summary=permit_summary(project_no, permit_type),
            verification_level=VerificationLevel.GOVERNMENT_RECORD,
            confidence=1.0,
        )
    ]
    # No PERMIT_APPLIED / PERMIT_FINALIZED: the weekly report carries only the
    # sold (issue) date. Comments stays in raw_payload only — see module doc.

    return NormalizedRecord(
        record_type=RECORD_TYPE,
        source_record_id=project_no,
        raw_payload=dict(record),
        normalized_address=normalized,
        hcad_account_id=None,  # the report has no HCAD account column
        event_candidates=candidates,
        raw_address=raw_address,
    )
