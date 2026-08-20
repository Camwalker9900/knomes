"""HCAD building-details import: beds/baths/quality/remodel year onto properties.

Source: member files of ``Real_building_land.zip`` published at
``settings.hcad_building_zip_url`` (download.hcad.org — an allowed host).
Layout verified against the live 2026 archive via bounded HTTP range requests
(central directory + decompressed member headers, 2026-08-19):

- ``building_res.txt`` — one row per (account, building). Tab-delimited, CRLF,
  header row, no quoting; ``acct`` is right-padded with spaces. Columns::

      acct  property_use_cd  bld_num  impr_tp  impr_mdl_cd  structure
      structure_dscr  dpr_val  cama_replacement_cost  accrued_depr_pct
      qa_cd  dscr  date_erected  eff  yr_remodel  yr_roll  appr_by  appr_dt
      notes  im_sq_ft  act_ar  heat_ar  gross_ar  eff_ar  base_ar  perimeter
      pct  bld_adj  rcnld  size_index  lump_sum_adj

  This import consumes ``qa_cd`` (quality code, e.g. ``B``) and ``yr_remodel``
  (``0`` or blank means "never remodeled" -> NULL). Per account, the row with
  the smallest ``bld_num`` (the primary improvement) wins.

- ``fixtures.txt`` — one row per (account, building, fixture type). Columns::

      acct  bld_num  type  type_dscr  units

  Room counts live here: ``type`` RMB (bedrooms), RMF (full baths), RMH (half
  baths); ``units`` is a decimal count like ``3.00``. Counts are summed across
  a property's buildings. Other fixture types (STY, RMT, FPW, ...) are ignored.

Both members are sorted by ``acct``; aggregation streams over contiguous
account runs and never loads a file into memory. Should an account reappear
later in a file, the later run's values win (same last-wins rule as the
real_acct import).

The import is a set-based ``UPDATE ... FROM (VALUES ...)`` keyed on
``properties.hcad_account_id``, guarded by ``IS DISTINCT FROM`` so re-imports
touch zero rows (idempotent; ``updated_at`` only moves when a value changes).
It never creates properties — accounts without a bootstrapped property are
counted as unmatched — and it emits no ledger events. Garbage rows (wrong
field count, missing account, non-numeric numerics) are rejected and counted,
never fatal.

CLI::

    python -m app.ingestion.hcad.building --file building_res.txt --file fixtures.txt

Each ``--file`` is a member file extracted from the zip; the kind is detected
from its header row.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

from sqlalchemy import Integer, Text, column, func, or_, select, update, values
from sqlalchemy.orm import Session

from app.models import Property

logger = logging.getLogger(__name__)

HCAD_SOURCE_NAME: Final[str] = "hcad"

KIND_BUILDING_RES: Final[str] = "building_res"
KIND_FIXTURES: Final[str] = "fixtures"

# Header columns each member kind must carry (extra columns are ignored, so
# minor layout additions don't break the import).
BUILDING_RES_REQUIRED: Final[frozenset[str]] = frozenset(
    {"acct", "bld_num", "qa_cd", "yr_remodel"}
)
FIXTURES_REQUIRED: Final[frozenset[str]] = frozenset({"acct", "bld_num", "type", "units"})

# fixtures.txt room fixture types -> properties column.
ROOM_TYPES: Final[dict[str, str]] = {
    "RMB": "bedrooms",
    "RMF": "bathrooms_full",
    "RMH": "bathrooms_half",
}

SNAPSHOT_ENCODING: Final[str] = "utf-8"
DEFAULT_BATCH_SIZE: Final[int] = 1000


class BuildingParseError(ValueError):
    """The member file itself is unusable (empty, or unrecognized header)."""


class BuildingRowError(ValueError):
    """One row failed validation and must be rejected (counted, never fatal)."""


@dataclass
class BuildingImportStats:
    """Counters for one member-file import pass."""

    kind: str
    rows_read: int = 0
    rows_rejected: int = 0
    accounts_seen: int = 0
    accounts_matched: int = 0
    accounts_unmatched: int = 0
    properties_updated: int = 0
    properties_unchanged: int = 0


@dataclass(frozen=True)
class BuildingDetails:
    """Per-account values reduced from building_res.txt (primary building wins)."""

    quality_code: str | None
    year_remodeled: int | None


@dataclass(frozen=True)
class RoomCounts:
    """Per-account room totals summed from fixtures.txt RMB/RMF/RMH rows.

    ``None`` means the file carried no row of that type for the account —
    absence of data, never a manufactured zero.
    """

    bedrooms: int | None
    bathrooms_full: int | None
    bathrooms_half: int | None


def open_member(path: Path) -> TextIO:
    """Open a Real_building_land member file for streaming.

    Real exports are effectively ASCII for the consumed columns; a stray byte
    must never abort a run, so decoding errors are replaced.
    """
    return path.open("r", encoding=SNAPSHOT_ENCODING, errors="replace")


def detect_member_kind(header_fields: Sequence[str]) -> str:
    """Classify a member file by its header row; raises BuildingParseError."""
    names = {name.strip() for name in header_fields}
    if BUILDING_RES_REQUIRED <= names:
        return KIND_BUILDING_RES
    if FIXTURES_REQUIRED <= names:
        return KIND_FIXTURES
    raise BuildingParseError(
        "unrecognized Real_building_land member header: expected building_res.txt "
        f"(columns {sorted(BUILDING_RES_REQUIRED)}) or fixtures.txt "
        f"(columns {sorted(FIXTURES_REQUIRED)}); got {sorted(names)[:10]}"
    )


def _split_header(line: str | None) -> list[str]:
    if line is None or not line.strip():
        raise BuildingParseError("member file is empty (no header row)")
    return [name.strip() for name in line.rstrip("\r\n").split("\t")]


def _rows(
    lines: Iterable[str], header: Sequence[str], stats: BuildingImportStats
) -> Iterator[dict[str, str]]:
    """Stream data rows as dicts; wrong field count / blank acct rejected+counted."""
    width = len(header)
    for line in lines:
        stripped = line.rstrip("\r\n")
        if not stripped:
            continue
        fields = stripped.split("\t")
        stats.rows_read += 1
        if len(fields) != width or not fields[0].strip():
            stats.rows_rejected += 1
            continue
        yield dict(zip(header, fields))


def _int_field(row: dict[str, str], key: str, acct: str) -> int:
    cleaned = row.get(key, "").strip()
    try:
        value = int(cleaned)
    except ValueError as exc:
        raise BuildingRowError(f"account {acct}: {key} is not an integer: {cleaned!r}") from exc
    if value < 0:
        raise BuildingRowError(f"account {acct}: {key} is negative: {value}")
    return value


def _units_field(row: dict[str, str], acct: str) -> int:
    cleaned = row.get("units", "").strip()
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise BuildingRowError(f"account {acct}: units is not numeric: {cleaned!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise BuildingRowError(f"account {acct}: units is not a valid count: {cleaned!r}")
    return round(value)


# ---------------------------------------------------------------------------
# Streaming per-account reduction (files are sorted by acct; contiguous runs)
# ---------------------------------------------------------------------------


def _reduce_building_res(
    rows: Iterator[dict[str, str]], stats: BuildingImportStats
) -> Iterator[tuple[str, BuildingDetails]]:
    """Reduce contiguous building_res rows per account: smallest bld_num wins."""
    current_acct: str | None = None
    best_bld_num: int | None = None
    best: BuildingDetails | None = None
    for row in rows:
        acct = row["acct"].strip()
        try:
            bld_num = _int_field(row, "bld_num", acct)
            year_remodeled: int | None = _int_field(row, "yr_remodel", acct)
        except BuildingRowError as exc:
            stats.rows_rejected += 1
            logger.warning(
                "hcad building row rejected",
                extra={"source_name": HCAD_SOURCE_NAME, "acct": acct, "error": str(exc)},
            )
            continue
        if year_remodeled == 0:  # HCAD encodes "never remodeled" as 0
            year_remodeled = None
        details = BuildingDetails(
            quality_code=row.get("qa_cd", "").strip() or None,
            year_remodeled=year_remodeled,
        )
        if acct != current_acct:
            if current_acct is not None and best is not None:
                yield current_acct, best
            current_acct, best_bld_num, best = acct, bld_num, details
        elif best_bld_num is None or bld_num < best_bld_num:
            best_bld_num, best = bld_num, details
    if current_acct is not None and best is not None:
        yield current_acct, best


def _reduce_fixtures(
    rows: Iterator[dict[str, str]], stats: BuildingImportStats
) -> Iterator[tuple[str, RoomCounts]]:
    """Sum contiguous fixtures RMB/RMF/RMH rows per account across buildings."""

    def _emit(acct: str, sums: dict[str, int]) -> tuple[str, RoomCounts]:
        return acct, RoomCounts(
            bedrooms=sums.get("bedrooms"),
            bathrooms_full=sums.get("bathrooms_full"),
            bathrooms_half=sums.get("bathrooms_half"),
        )

    current_acct: str | None = None
    sums: dict[str, int] = {}
    for row in rows:
        acct = row["acct"].strip()
        if acct != current_acct:
            if current_acct is not None and sums:
                yield _emit(current_acct, sums)
            current_acct, sums = acct, {}
        field = ROOM_TYPES.get(row.get("type", "").strip())
        if field is None:  # non-room fixture types are ignored, not rejected
            continue
        try:
            units = _units_field(row, acct)
        except BuildingRowError as exc:
            stats.rows_rejected += 1
            logger.warning(
                "hcad fixtures row rejected",
                extra={"source_name": HCAD_SOURCE_NAME, "acct": acct, "error": str(exc)},
            )
            continue
        sums[field] = sums.get(field, 0) + units
    if current_acct is not None and sums:
        yield _emit(current_acct, sums)


# ---------------------------------------------------------------------------
# Set-based UPDATE ... FROM (VALUES ...) keyed on hcad_account_id
# ---------------------------------------------------------------------------


def _flush(
    session: Session,
    batch: dict[str, tuple[object, ...]],
    columns: Sequence[str],
    stats: BuildingImportStats,
) -> None:
    """Apply one batch: count matches, then update only rows whose values differ."""
    if not batch:
        return
    vals = (
        values(
            column("acct", Text),
            *(
                column(name, Text() if name == "quality_code" else Integer())
                for name in columns
            ),
            name="hcad_building_rows",
        )
        .data([(acct, *row) for acct, row in sorted(batch.items())])
    )
    matched = session.execute(
        select(func.count()).select_from(
            vals.join(Property, Property.hcad_account_id == vals.c.acct)
        )
    ).scalar_one()
    stats.accounts_matched += matched
    stats.accounts_unmatched += len(batch) - matched

    changed = or_(
        *(
            getattr(Property, name).is_distinct_from(getattr(vals.c, name))
            for name in columns
        )
    )
    stmt = (
        update(Property)
        .where(Property.hcad_account_id == vals.c.acct, changed)
        .values(
            {name: getattr(vals.c, name) for name in columns} | {"updated_at": func.now()}
        )
        .returning(Property.id)
    )
    updated = len(session.execute(stmt).all())
    stats.properties_updated += updated
    stats.properties_unchanged += matched - updated
    batch.clear()


def _apply(
    session: Session,
    aggregates: Iterator[tuple[str, tuple[object, ...]]],
    columns: Sequence[str],
    stats: BuildingImportStats,
    batch_size: int,
) -> None:
    batch: dict[str, tuple[object, ...]] = {}
    for acct, row in aggregates:
        if acct not in batch:
            stats.accounts_seen += 1
        batch[acct] = row  # later occurrence of an account wins
        if len(batch) >= batch_size:
            _flush(session, batch, columns, stats)
    _flush(session, batch, columns, stats)


def import_building_file(
    session: Session, file: Path, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> BuildingImportStats:
    """Stream one Real_building_land member file into properties. Idempotent.

    Detects the member kind from the header row, reduces rows per account, and
    applies set-based updates keyed on ``hcad_account_id``. Returns counters;
    raises :class:`BuildingParseError` only when the file itself is unusable.
    """
    with open_member(file) as fh:
        header = _split_header(next(fh, None))
        kind = detect_member_kind(header)
        stats = BuildingImportStats(kind=kind)
        rows = _rows(fh, header, stats)
        if kind == KIND_BUILDING_RES:
            aggregates: Iterator[tuple[str, tuple[object, ...]]] = (
                (acct, (d.quality_code, d.year_remodeled))
                for acct, d in _reduce_building_res(rows, stats)
            )
            columns: tuple[str, ...] = ("quality_code", "year_remodeled")
        else:
            aggregates = (
                (acct, (r.bedrooms, r.bathrooms_full, r.bathrooms_half))
                for acct, r in _reduce_fixtures(rows, stats)
            )
            columns = ("bedrooms", "bathrooms_full", "bathrooms_half")
        _apply(session, aggregates, columns, stats, batch_size)
    logger.info(
        "hcad building details imported",
        extra={
            "source_name": HCAD_SOURCE_NAME,
            "member_kind": stats.kind,
            "file": str(file),
            "rows_read": stats.rows_read,
            "rows_rejected": stats.rows_rejected,
            "accounts_seen": stats.accounts_seen,
            "accounts_matched": stats.accounts_matched,
            "accounts_unmatched": stats.accounts_unmatched,
            "properties_updated": stats.properties_updated,
            "properties_unchanged": stats.properties_unchanged,
        },
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m app.ingestion.hcad.building --file PATH [--file PATH ...]``."""
    from app.core.db import SessionLocal
    from app.core.logging import configure_logging

    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.hcad.building",
        description=(
            "Import HCAD building details (beds/baths/quality/remodel year) from "
            "Real_building_land member files (building_res.txt / fixtures.txt)."
        ),
    )
    parser.add_argument(
        "--file",
        dest="files",
        type=Path,
        action="append",
        required=True,
        help="member file extracted from Real_building_land.zip; repeatable",
    )
    args = parser.parse_args(argv)
    configure_logging()
    session = SessionLocal()
    try:
        for file in args.files:
            try:
                stats = import_building_file(session, file)
            except (BuildingParseError, OSError) as exc:
                session.rollback()
                print(f"error: {file}: {exc}", file=sys.stderr)
                return 2
            session.commit()
            print(
                f"hcad building import ({stats.kind}) {file}:"
                f" rows_read={stats.rows_read} rows_rejected={stats.rows_rejected}"
                f" accounts_seen={stats.accounts_seen}"
                f" matched={stats.accounts_matched} unmatched={stats.accounts_unmatched}"
                f" updated={stats.properties_updated} unchanged={stats.properties_unchanged}"
            )
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
