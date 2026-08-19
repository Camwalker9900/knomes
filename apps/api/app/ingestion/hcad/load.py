"""Set-based loading of HCAD real_acct data: staging COPY, property upsert, aliases.

Scale path (used by ``app.ingestion.hcad.sync``):

1. :func:`load_staging` — psycopg ``COPY`` streams the snapshot into the
   UNLOGGED ``hcad_staging`` table (created ``IF NOT EXISTS``, truncated per
   run). Rows are projected onto the required columns while streaming.
2. :func:`upsert_from_staging` — reads staging in ``seq`` order (so a
   duplicate account's **last** occurrence wins), validates each row in Python
   (address normalization has a single source of truth: ``app.lib.address``),
   and applies one multi-row ``INSERT ... ON CONFLICT (hcad_account_id) DO
   UPDATE`` per batch, plus one set-based alias insert per batch. Rows failing
   validation are counted as rejected, never fatal.
3. :func:`insert_source_records` / :func:`attach_source_record_properties` —
   raw provenance rows deduped on the ``(source_name, source_record_id,
   content_hash)`` unique triple via ``ON CONFLICT DO NOTHING``, then linked
   to their properties with one set-based ``UPDATE ... FROM``.

:func:`upsert_properties` is the same upsert keyed on already-normalized
records for callers that skip staging (e.g. tests or the generic runner).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg
from sqlalchemy import (
    BigInteger,
    Column,
    Identity,
    MetaData,
    Table,
    Text,
    column,
    func,
    insert,
    literal,
    or_,
    select,
    text,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import Executable, literal_column

from app.ingestion.base import NormalizedRecord, RawSnapshot
from app.ingestion.hcad.normalize import (
    HCAD_PARSER_VERSION,
    HCAD_RECORD_TYPE,
    HCAD_SOURCE_NAME,
    HCADRowError,
    ParsedProperty,
    parse_property_fields,
)
from app.ingestion.hcad.parse import (
    OPTIONAL_COLUMNS,
    REQUIRED_COLUMNS,
    ParseStats,
    open_real_acct,
    parse_rows,
)
from app.ingestion.runner import content_hash_for_payload
from app.models import Property, PropertyAddress, SourceRecord

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1000

# Staging lives outside Base.metadata on purpose: it is a runtime working
# table, not part of the migrated canonical schema.
staging_metadata = MetaData()

# All columns staged: required plus whichever optional ones the layout carries
# (absent columns COPY as empty strings and parse as None downstream).
STAGING_COLUMNS: tuple[str, ...] = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

hcad_staging = Table(
    "hcad_staging",
    staging_metadata,
    Column("seq", BigInteger, Identity(always=True), primary_key=True),
    *(Column(name, Text) for name in STAGING_COLUMNS),
    prefixes=["UNLOGGED"],
)

_UPDATABLE_COLUMNS: tuple[str, ...] = (
    "address_line1",
    "unit",
    "city",
    "state",
    "postal_code",
    "normalized_address",
    "address_hash",
    "year_built",
    "building_sqft",
    "lot_sqft",
    "property_type",
)


@dataclass
class UpsertStats:
    """Counters for one property-upsert pass."""

    rows_valid: int = 0
    rows_rejected: int = 0
    properties_inserted: int = 0
    properties_updated: int = 0
    properties_unchanged: int = 0
    aliases_inserted: int = 0


def ensure_staging(conn: Connection) -> None:
    """(Re)create the UNLOGGED hcad_staging table with the current layout."""
    hcad_staging.drop(conn, checkfirst=True)
    hcad_staging.create(conn)


def load_staging(conn: Connection, file: Path) -> ParseStats:
    """COPY a real_acct snapshot into hcad_staging via psycopg, streaming.

    Rows are parsed one line at a time and written straight into the COPY
    protocol — the file is never loaded into memory. Returns parse counters
    (``rows_yielded`` = rows copied, ``rows_skipped`` = malformed rows).
    """
    ensure_staging(conn)
    conn.execute(text("TRUNCATE TABLE hcad_staging"))
    stats = ParseStats()
    driver = conn.connection.driver_connection
    assert isinstance(driver, psycopg.Connection), "hcad staging COPY requires psycopg"
    columns_sql = ", ".join(f'"{name}"' for name in STAGING_COLUMNS)
    copy_sql = f"COPY hcad_staging ({columns_sql}) FROM STDIN"
    with open_real_acct(file) as fh, driver.cursor() as cur, cur.copy(copy_sql) as copy:
        for row in parse_rows(fh, stats=stats):
            copy.write_row(tuple(row.get(name, "") for name in STAGING_COLUMNS))
    logger.info(
        "hcad staging loaded",
        extra={
            "source_name": HCAD_SOURCE_NAME,
            "rows_copied": stats.rows_yielded,
            "rows_malformed": stats.rows_skipped,
        },
    )
    return stats


def _property_row(parsed: ParsedProperty) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "hcad_account_id": parsed.hcad_account_id,
        "address_line1": parsed.address_line1,
        "unit": parsed.unit,
        "city": parsed.city,
        "state": parsed.state,
        "postal_code": parsed.postal_code,
        "normalized_address": parsed.normalized_address,
        "address_hash": parsed.address_hash,
        "year_built": parsed.year_built,
        "building_sqft": parsed.building_sqft,
        "lot_sqft": parsed.lot_sqft,
        "property_type": parsed.property_type,
    }


def _flush_batch(
    session: Session, batch: dict[str, ParsedProperty], stats: UpsertStats
) -> None:
    """One set-based UPSERT + one set-based alias insert for a deduped batch."""
    if not batch:
        return
    rows = [_property_row(parsed) for parsed in batch.values()]
    stmt = pg_insert(Property).values(rows)
    excluded = stmt.excluded
    set_: dict[str, object] = {
        name: getattr(excluded, name) for name in _UPDATABLE_COLUMNS
    }
    set_["updated_at"] = func.now()
    changed = or_(
        *(
            getattr(Property, name).is_distinct_from(getattr(excluded, name))
            for name in _UPDATABLE_COLUMNS
        )
    )
    upsert: Executable = stmt.on_conflict_do_update(
        index_elements=[Property.hcad_account_id], set_=set_, where=changed
    ).returning(literal_column("(xmax = 0)").label("inserted"))
    flags = session.execute(upsert).scalars().all()
    inserted = sum(1 for flag in flags if flag)
    stats.properties_inserted += inserted
    stats.properties_updated += len(flags) - inserted
    stats.properties_unchanged += len(rows) - len(flags)
    stats.aliases_inserted += _insert_aliases(session, list(batch.values()))


def _insert_aliases(session: Session, parsed: Sequence[ParsedProperty]) -> int:
    """Insert property_addresses alias rows (source 'hcad') not already present.

    One ``INSERT ... SELECT ... FROM (VALUES ...)`` per batch; presence is
    keyed on (property_id, normalized_address) so re-imports add nothing.
    """
    deduped: dict[tuple[str, str], tuple[str, str, str]] = {}
    for item in parsed:
        deduped.setdefault(
            (item.hcad_account_id, item.normalized_address),
            (item.hcad_account_id, item.raw_address, item.normalized_address),
        )
    if not deduped:
        return 0
    alias_values = (
        values(
            column("acct", Text),
            column("raw_address", Text),
            column("normalized_address", Text),
            name="hcad_alias_rows",
        )
        .data(sorted(deduped.values()))
    )
    already_present = (
        select(PropertyAddress.id)
        .where(
            PropertyAddress.property_id == Property.id,
            PropertyAddress.normalized_address == alias_values.c.normalized_address,
        )
        .exists()
    )
    selectable = (
        select(
            func.gen_random_uuid(),
            Property.id,
            alias_values.c.raw_address,
            alias_values.c.normalized_address,
            literal(HCAD_SOURCE_NAME),
        )
        .join_from(alias_values, Property, Property.hcad_account_id == alias_values.c.acct)
        .where(~already_present)
    )
    result = session.execute(
        insert(PropertyAddress)
        .from_select(
            ["id", "property_id", "raw_address", "normalized_address", "source"],
            selectable,
        )
        .returning(PropertyAddress.id)
    )
    return len(result.all())


def _upsert_parsed_stream(
    session: Session,
    parsed_rows: Iterator[ParsedProperty | None],
    *,
    batch_size: int,
) -> UpsertStats:
    """Shared upsert loop: ``None`` items count as rejected, batches dedupe by acct."""
    stats = UpsertStats()
    batch: dict[str, ParsedProperty] = {}
    for parsed in parsed_rows:
        if parsed is None:
            stats.rows_rejected += 1
            continue
        stats.rows_valid += 1
        batch[parsed.hcad_account_id] = parsed  # last occurrence wins
        if len(batch) >= batch_size:
            _flush_batch(session, batch, stats)
            batch = {}
    _flush_batch(session, batch, stats)
    logger.info(
        "hcad properties upserted",
        extra={
            "source_name": HCAD_SOURCE_NAME,
            "rows_valid": stats.rows_valid,
            "rows_rejected": stats.rows_rejected,
            "properties_inserted": stats.properties_inserted,
            "properties_updated": stats.properties_updated,
            "properties_unchanged": stats.properties_unchanged,
            "aliases_inserted": stats.aliases_inserted,
        },
    )
    return stats


def _parse_or_none(raw: dict[str, object]) -> ParsedProperty | None:
    try:
        return parse_property_fields(raw)
    except HCADRowError as exc:
        logger.warning(
            "hcad row rejected",
            extra={
                "source_name": HCAD_SOURCE_NAME,
                "acct": str(raw.get("acct", "")),
                "error": str(exc),
            },
        )
        return None


def upsert_properties(
    session: Session,
    records: Iterable[NormalizedRecord],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> UpsertStats:
    """Set-based UPSERT of properties keyed on hcad_account_id, plus aliases.

    ``records`` are :class:`NormalizedRecord` items whose ``raw_payload``
    carries the real_acct fields; each is re-validated (rejects counted).
    """
    return _upsert_parsed_stream(
        session,
        (_parse_or_none(dict(rec.raw_payload)) for rec in records),
        batch_size=batch_size,
    )


def upsert_from_staging(
    session: Session, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> UpsertStats:
    """Set-based UPSERT of properties from hcad_staging (the COPY scale path).

    Streams staging rows in ``seq`` order via a server-side cursor so a
    duplicate account's last file occurrence wins, both within a batch (dict
    keyed by acct) and across batches (later batch upserts overwrite earlier).
    """
    stmt = (
        select(*(hcad_staging.c[name] for name in STAGING_COLUMNS))
        .order_by(hcad_staging.c.seq)
        .execution_options(yield_per=batch_size)
    )
    result = session.execute(stmt)
    return _upsert_parsed_stream(
        session,
        (_parse_or_none(dict(row)) for row in result.mappings()),
        batch_size=batch_size,
    )


def _flush_source_records(session: Session, batch: list[dict[str, object]]) -> int:
    stmt = (
        pg_insert(SourceRecord)
        .values(batch)
        .on_conflict_do_nothing(
            index_elements=[
                SourceRecord.source_name,
                SourceRecord.source_record_id,
                SourceRecord.content_hash,
            ]
        )
        # RETURNING yields only the rows actually inserted (conflicts skipped),
        # giving a reliable created count regardless of driver rowcount quirks.
        .returning(SourceRecord.id)
    )
    result = session.execute(stmt)
    return len(result.all())


def insert_source_records(
    session: Session,
    snapshot: RawSnapshot,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Insert raw per-row provenance records, deduped on content_hash.

    Every parseable row (including rows later rejected by field validation)
    is retained exactly as received — provenance is never lost. Dedup rides
    the ``(source_name, source_record_id, content_hash)`` unique constraint
    via ``ON CONFLICT DO NOTHING``; returns the number actually created.
    """
    created = 0
    batch: list[dict[str, object]] = []
    with open_real_acct(snapshot.storage_path) as fh:
        for row in parse_rows(fh):
            payload: dict[str, object] = dict(row)
            batch.append(
                {
                    "id": uuid.uuid4(),
                    "source_name": HCAD_SOURCE_NAME,
                    "source_record_id": row["acct"].strip(),
                    "record_type": HCAD_RECORD_TYPE,
                    "raw_payload": payload,
                    "source_url": snapshot.source_url,
                    "retrieved_at": snapshot.retrieved_at,
                    "content_hash": content_hash_for_payload(payload),
                    "parser_version": HCAD_PARSER_VERSION,
                }
            )
            if len(batch) >= batch_size:
                created += _flush_source_records(session, batch)
                batch = []
    if batch:
        created += _flush_source_records(session, batch)
    logger.info(
        "hcad source records recorded",
        extra={"source_name": HCAD_SOURCE_NAME, "source_records_created": created},
    )
    return created


def attach_source_record_properties(session: Session) -> int:
    """Set-based link of hcad source_records to their properties by account id."""
    stmt = (
        update(SourceRecord)
        .where(
            SourceRecord.source_name == HCAD_SOURCE_NAME,
            SourceRecord.property_id.is_(None),
            Property.hcad_account_id == SourceRecord.source_record_id,
        )
        .values(property_id=Property.id)
        .returning(SourceRecord.id)
    )
    result = session.execute(stmt)
    return len(result.all())
