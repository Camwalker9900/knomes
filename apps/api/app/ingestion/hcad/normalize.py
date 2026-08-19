"""HCAD real_acct normalization: raw row dicts -> canonical property field shapes.

HCAD is the CANONICAL PROPERTY BOOTSTRAP: its records create and update
``properties`` rows rather than only attaching ledger events, so
:meth:`HCADAdapter.normalize` emits an **empty** ``event_candidates`` list —
the property bootstrap produces no ledger events in this phase.

Field validation is strict where it matters: a garbage or negative square
footage rejects the row (raising :class:`HCADRowError`, which the pipelines
count without crashing), while a merely missing ``yr_impr`` yields
``year_built=None``.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Final

from app.core.config import settings
from app.ingestion.base import NormalizedRecord, RawSnapshot, SourceAdapter
from app.ingestion.hcad.download import DEFAULT_RAW_DIR, fetch_snapshot
from app.ingestion.hcad.parse import open_real_acct, parse_rows
from app.lib.address import address_hash, normalize_address

HCAD_SOURCE_NAME: Final[str] = "hcad"
HCAD_RECORD_TYPE: Final[str] = "real_acct"
HCAD_PARSER_VERSION: Final[str] = "1.1.0"

DEFAULT_CITY: Final[str] = "HOUSTON"
DEFAULT_STATE: Final[str] = "TX"


class HCADRowError(ValueError):
    """One real_acct row failed field validation and must be rejected (not fatal)."""


@dataclass(frozen=True)
class ParsedProperty:
    """Canonical property column values parsed out of one real_acct row."""

    hcad_account_id: str
    address_line1: str
    unit: str | None
    city: str
    state: str
    postal_code: str
    raw_address: str
    normalized_address: str
    address_hash: str
    year_built: int | None
    building_sqft: int | None
    lot_sqft: int | None
    property_type: str | None


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    return "" if value is None else str(value).strip()


def _int_or_none(raw: Mapping[str, object], key: str, acct: str) -> int | None:
    cleaned = _text(raw, key).replace(",", "")
    if not cleaned:
        return None
    try:
        value = int(cleaned)
    except ValueError as exc:
        raise HCADRowError(f"account {acct}: {key} is not an integer: {cleaned!r}") from exc
    if value < 0:
        raise HCADRowError(f"account {acct}: {key} is negative: {value}")
    return value


def _land_sqft_or_none(raw: Mapping[str, object], key: str, acct: str) -> int | None:
    cleaned = _text(raw, key).replace(",", "")
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise HCADRowError(f"account {acct}: {key} is not numeric: {cleaned!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise HCADRowError(f"account {acct}: {key} is not a valid area: {cleaned!r}")
    return int(value)


def parse_property_fields(raw: Mapping[str, object]) -> ParsedProperty:
    """Parse one raw real_acct row into canonical property fields.

    Raises :class:`HCADRowError` for rows that must be rejected: missing
    account, no situs street parts, or garbage/negative numeric fields.
    """
    acct = _text(raw, "acct")
    if not acct:
        raise HCADRowError("row has no acct (HCAD account id)")

    street_parts = [
        part
        for part in (
            _text(raw, "str_num"),
            _text(raw, "str_pfx"),
            _text(raw, "str"),
            _text(raw, "str_sfx"),
            _text(raw, "str_sfx_dir"),
        )
        if part
    ]
    if not street_parts:
        raise HCADRowError(f"account {acct}: no situs street parts (str_num/str/str_sfx)")
    address_line1 = " ".join(street_parts)
    unit = _text(raw, "str_unit") or None
    raw_address = f"{address_line1} UNIT {unit}" if unit else address_line1
    normalized = normalize_address(raw_address)

    return ParsedProperty(
        hcad_account_id=acct,
        address_line1=address_line1,
        unit=unit,
        city=_text(raw, "site_addr_2") or DEFAULT_CITY,
        state=DEFAULT_STATE,
        postal_code=_text(raw, "site_addr_3"),
        raw_address=raw_address,
        normalized_address=normalized,
        address_hash=address_hash(normalized),
        year_built=_int_or_none(raw, "yr_impr", acct),
        building_sqft=_int_or_none(
            raw, "im_sq_ft" if _text(raw, "im_sq_ft") else "bld_ar", acct
        ),
        lot_sqft=_land_sqft_or_none(raw, "land_ar", acct),
        property_type=_text(raw, "state_class") or None,
    )


def _iter_snapshot_rows(path: Path) -> Iterator[dict[str, Any]]:
    with open_real_acct(path) as fh:
        yield from parse_rows(fh)


class HCADAdapter(SourceAdapter):
    """Adapter for the HCAD real_acct export — the canonical property bootstrap."""

    source_name: ClassVar[str] = HCAD_SOURCE_NAME
    parser_version: ClassVar[str] = HCAD_PARSER_VERSION

    async def fetch(self) -> RawSnapshot:
        """Download the configured HCAD export (raises ValueError when unconfigured)."""
        return fetch_snapshot(
            settings.hcad_download_url or None, DEFAULT_RAW_DIR / "downloads"
        )

    def parse(self, snapshot: RawSnapshot) -> Iterator[dict[str, Any]]:
        """Stream raw row dicts out of a snapshot file without loading it whole."""
        return _iter_snapshot_rows(snapshot.storage_path)

    def normalize(self, record: dict[str, Any]) -> NormalizedRecord:
        """Map one raw row to the canonical shape; raises HCADRowError to reject."""
        fields = parse_property_fields(record)
        return NormalizedRecord(
            record_type=HCAD_RECORD_TYPE,
            source_record_id=fields.hcad_account_id,
            raw_payload=dict(record),
            normalized_address=fields.normalized_address,
            hcad_account_id=fields.hcad_account_id,
            event_candidates=[],  # property bootstrap: no ledger events in this phase
            raw_address=fields.raw_address,
        )
