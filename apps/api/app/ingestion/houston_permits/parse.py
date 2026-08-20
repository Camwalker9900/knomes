"""Read Houston Permitting Center weekly permit activity report files.

LAYOUT ASSUMPTION (verified 2026-08-19 against a real report downloaded from
https://www.houstonpermittingcenter.org/sold-permits-search — e.g.
https://www.houstonpermittingcenter.org/sites/g/files/nwywnm431/files/2026-07/July%2013-19.xlsx):

The city publishes one XLSX per week ("Web eReport"), single worksheet:

- row 1: ``Web eReport``
- row 2: ``From: YYYY/MM/DD``
- row 3: ``To : YYYY/MM/DD``
- row 4: blank
- row 5: header ``Zip Code | Permit Date | Permit Type | Project No | Address | Comments``
- rows 6..N: one row per permit sold (issued) in the reporting week
- footer: a few disclaimer text rows in column A only ("* Information provided
  to City by applicant ... The City does not confirm or verify ...")

``iter_report_rows`` locates the header row by its column names (so the exact
preamble length may drift), maps each subsequent row to a dict keyed by the
header texts, and skips structural non-data rows (blank rows and the footer
disclaimer, recognized by having no Permit Date, no Project No, and no
Address). It reads the native ``.xlsx`` directly with the standard library
(zipfile + ElementTree — no third-party Excel dependency) and also accepts a
``.csv`` export of the same sheet, row for row.
"""

from __future__ import annotations

import csv
import pathlib
import zipfile
from collections.abc import Iterator
from typing import Final
from xml.etree import ElementTree as ET

SPREADSHEET_NS: Final[str] = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

# Header texts as printed in the real report (canonical raw_payload keys).
ZIP_COLUMN: Final[str] = "Zip Code"
DATE_COLUMN: Final[str] = "Permit Date"
TYPE_COLUMN: Final[str] = "Permit Type"
NUMBER_COLUMN: Final[str] = "Project No"
ADDRESS_COLUMN: Final[str] = "Address"
COMMENTS_COLUMN: Final[str] = "Comments"
EXPECTED_COLUMNS: Final[tuple[str, ...]] = (
    ZIP_COLUMN,
    DATE_COLUMN,
    TYPE_COLUMN,
    NUMBER_COLUMN,
    ADDRESS_COLUMN,
    COMMENTS_COLUMN,
)
# A row is the header once it carries at least these columns (case-insensitive).
_HEADER_REQUIRED: Final[frozenset[str]] = frozenset(
    name.casefold() for name in (DATE_COLUMN, NUMBER_COLUMN, ADDRESS_COLUMN)
)
_CANONICAL_BY_CASEFOLD: Final[dict[str, str]] = {name.casefold(): name for name in EXPECTED_COLUMNS}


def _column_index(cell_ref: str) -> int | None:
    """0-based column index from an A1-style cell reference ("C7" -> 2)."""
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    if not letters:
        return None
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _cell_text(cell: ET.Element, shared: list[str]) -> str | None:
    """Decode one <c> element: shared strings, inline strings, and plain values."""
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        texts = [t.text or "" for t in cell.iter(f"{{{SPREADSHEET_NS}}}t")]
        return "".join(texts) if texts else None
    value = cell.find(f"{{{SPREADSHEET_NS}}}v")
    if value is None or value.text is None:
        return None
    if cell_type == "s":
        return shared[int(value.text)]
    return value.text


def _load_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for si in root.findall(f"{{{SPREADSHEET_NS}}}si"):
        strings.append("".join(t.text or "" for t in si.iter(f"{{{SPREADSHEET_NS}}}t")))
    return strings


def _first_sheet_member(archive: zipfile.ZipFile) -> str:
    members = sorted(
        name
        for name in archive.namelist()
        if name.startswith("xl/worksheets/") and name.endswith(".xml")
    )
    if not members:
        raise ValueError("xlsx file has no worksheets")
    if "xl/worksheets/sheet1.xml" in members:
        return "xl/worksheets/sheet1.xml"
    return members[0]


def iter_xlsx_rows(path: pathlib.Path) -> Iterator[list[str | None]]:
    """Yield the first worksheet's rows as lists of cell texts (None = blank)."""
    with zipfile.ZipFile(path) as archive:
        shared = _load_shared_strings(archive)
        root = ET.fromstring(archive.read(_first_sheet_member(archive)))
        for row in root.iter(f"{{{SPREADSHEET_NS}}}row"):
            cells: dict[int, str | None] = {}
            position = 0
            for cell in row.findall(f"{{{SPREADSHEET_NS}}}c"):
                index = _column_index(cell.get("r") or "")
                if index is None:
                    index = position  # producer omitted r= — fall back to order
                position = index + 1
                cells[index] = _cell_text(cell, shared)
            if not cells:
                yield []
                continue
            width = max(cells) + 1
            yield [cells.get(i) for i in range(width)]


def iter_csv_rows(path: pathlib.Path) -> Iterator[list[str | None]]:
    """Yield rows of a CSV export of the report sheet ("" cells become None)."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.reader(handle):
            yield [cell if cell.strip() else None for cell in row]


def _find_header(row: list[str | None]) -> dict[int, str] | None:
    """Map column index -> canonical header name if this row is the header."""
    mapping: dict[int, str] = {}
    seen: set[str] = set()
    for index, cell in enumerate(row):
        if cell is None:
            continue
        text = cell.strip()
        key = text.casefold()
        canonical = _CANONICAL_BY_CASEFOLD.get(key, text)
        if canonical.casefold() in seen:
            continue  # first occurrence of a duplicated header wins
        seen.add(canonical.casefold())
        mapping[index] = canonical
    if _HEADER_REQUIRED <= {name.casefold() for name in mapping.values()}:
        return mapping
    return None


def _is_data_row(record: dict[str, str | None]) -> bool:
    """True when the row carries permit content (not a footer/blank row)."""
    return any(
        record.get(column) is not None for column in (DATE_COLUMN, NUMBER_COLUMN, ADDRESS_COLUMN)
    )


def iter_report_rows(path: pathlib.Path) -> Iterator[dict[str, str | None]]:
    """Yield permit rows of a weekly report file as header-keyed dicts.

    Accepts the published ``.xlsx`` directly or a ``.csv`` export of the same
    sheet. Raises ValueError when no header row is present (wrong file).
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        rows = iter_csv_rows(path)
    elif suffix == ".xlsx":
        rows = iter_xlsx_rows(path)
    else:
        raise ValueError(
            f"unsupported report file type {suffix!r} (expected .xlsx or .csv): {path}"
        )

    header: dict[int, str] | None = None
    for row in rows:
        if header is None:
            header = _find_header(row)
            continue
        record: dict[str, str | None] = dict.fromkeys(header.values())
        for index, name in header.items():
            value = row[index] if index < len(row) else None
            if value is not None:
                text = value.strip()
                record[name] = text or None
        if _is_data_row(record):
            yield record
    if header is None:
        raise ValueError(
            f"no header row found in {path} — expected columns"
            f" {', '.join(EXPECTED_COLUMNS)} (Houston weekly permit activity report)"
        )
