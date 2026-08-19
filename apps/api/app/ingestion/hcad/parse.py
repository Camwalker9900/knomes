"""Streaming parser for HCAD ``real_acct.txt`` exports.

LAYOUT ASSUMPTION (documented here so the fixture file stays pure data):

The Harris Central Appraisal District publishes ``Real_acct_owner.zip`` on its
public data downloads site; the archive contains ``real_acct.txt`` with this
shape:

- one record per line, TAB (0x09) delimited, **no quoting** — quote characters
  are literal data;
- the first line is a header row naming every column;
- the full export carries many more columns than this pipeline consumes; the
  parser reads by header name, so extra columns flow through untouched into
  the raw payload and column order does not matter;
- columns this pipeline requires (``REQUIRED_COLUMNS``):
  ``acct`` (13-digit account id — the property key),
  ``str_num``/``str``/``str_sfx``/``str_unit`` (situs street parts),
  ``site_addr_1`` (assembled situs street line), ``site_addr_2`` (situs city),
  ``site_addr_3`` (situs ZIP), ``state_class`` (state classification code,
  e.g. ``A1``), ``yr_impr`` (year of primary improvement), ``im_sq_ft``
  (improvement square feet), ``land_ar`` (land area in square feet),
  ``tot_mkt_val`` (total market value);
- numeric columns are plain digit strings and may be empty;
- real exports are Windows-1252-flavored text that is effectively ASCII for
  the columns above — snapshots are opened with ``errors="replace"`` so a
  stray byte can never abort a run (see :func:`open_real_acct`).

Rows whose field count does not match the header, or that carry no ``acct``,
are malformed: they are skipped and counted, never fatal. Parsing streams —
the file is never loaded into memory as a whole.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "acct",
    "str_num",
    "str",
    "str_sfx",
    "str_unit",
    "site_addr_1",
    "site_addr_2",
    "site_addr_3",
    "state_class",
    "yr_impr",
    "im_sq_ft",
    "land_ar",
    "tot_mkt_val",
)

SNAPSHOT_ENCODING: Final[str] = "utf-8"

_RESTKEY: Final[str] = "_extra_fields_"


class HCADParseError(ValueError):
    """The file itself is unusable (empty, or the header lacks required columns)."""


@dataclass
class ParseStats:
    """Row counters accumulated while streaming one real_acct file."""

    rows_yielded: int = 0
    rows_skipped: int = 0

    @property
    def rows_read(self) -> int:
        """Total data rows read: yielded plus malformed-skipped."""
        return self.rows_yielded + self.rows_skipped


def open_real_acct(path: Path) -> TextIO:
    """Open a real_acct snapshot for streaming (encoding per module docstring)."""
    return path.open("r", encoding=SNAPSHOT_ENCODING, errors="replace", newline="")


def parse_rows(
    source: Iterable[str], *, stats: ParseStats | None = None
) -> Iterator[dict[str, str]]:
    """Stream raw rows as dicts from an open real_acct file, one line at a time.

    ``source`` is any iterable of lines (an open text file works). Malformed
    rows — wrong field count, or missing ``acct`` — are skipped and counted in
    ``stats``; a missing header or missing required columns raises
    :class:`HCADParseError` on the first pull. This is a generator: the whole
    file is never materialized.
    """
    reader = csv.DictReader(
        source,
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
        restkey=_RESTKEY,
        restval=None,
    )
    fieldnames = reader.fieldnames  # consumes exactly the header line
    if fieldnames is None:
        raise HCADParseError("real_acct file is empty (no header row)")
    missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        raise HCADParseError(
            f"real_acct header is missing required columns: {', '.join(missing)}"
        )
    for row in reader:
        extras = row.pop(_RESTKEY, None)
        shape_ok = extras is None and all(isinstance(v, str) for v in row.values())
        acct = (row.get("acct") or "").strip() if shape_ok else ""
        if not shape_ok or not acct:
            if stats is not None:
                stats.rows_skipped += 1
            continue
        if stats is not None:
            stats.rows_yielded += 1
        yield {str(key): str(value) for key, value in row.items()}
