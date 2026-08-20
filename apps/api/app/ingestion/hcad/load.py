"""Set-based loading of HCAD real_acct data at full-county scale (~1.5M rows).

The pipeline makes ONE streaming pass over the snapshot file and then applies
a handful of set-based SQL statements — no per-row ORM work anywhere:

1. :func:`load_staging` — while psycopg ``COPY`` streams each row into the
   UNLOGGED ``hcad_staging`` table, Python computes everything the SQL side
   will need: the validated canonical property fields (address normalization
   keeps its single source of truth in ``app.lib.address``), the canonical
   JSON payload + its sha256 content hash (identical to
   ``app.ingestion.runner.content_hash_for_payload``), and the parsed
   ``new_own_dt`` ownership-transfer date. Rows failing field validation are
   staged with ``valid = false`` — they still get raw provenance, never a
   property.
2. :func:`upsert_staged_properties` — one ``INSERT ... SELECT ... ON CONFLICT
   (hcad_account_id) DO UPDATE`` over the last file occurrence per account
   (duplicates resolved by a window function on ``seq``), plus one set-based
   ``property_addresses`` alias insert keyed on
   (property_id, normalized_address).
3. :func:`insert_source_records` — one ``INSERT ... SELECT ... WHERE NOT
   EXISTS`` from staging into ``source_records``, deduped on the
   ``(source_name, source_record_id, content_hash)`` unique triple (with
   ``ON CONFLICT DO NOTHING`` as a backstop); then
   :func:`attach_source_record_properties` links records to properties with
   one ``UPDATE ... FROM``.
4. :func:`create_ownership_events` — one ``INSERT ... SELECT`` creating
   OWNERSHIP_TRANSFER ledger events for staged rows whose ``new_own_dt``
   parsed, deduped logically: an event is skipped when ANY existing
   OWNERSHIP_TRANSFER event (from any source) already sits on the same
   (property_id, event_date). A changed date on re-import therefore creates a
   new event for the new date while the old event is retained. Titles are the
   fixed public wording; owner names never appear (privacy spec §37).

:func:`upsert_properties` is the same staged upsert for callers that start
from already-normalized records instead of a snapshot file (e.g. tests).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    CursorResult,
    DateTime,
    Identity,
    Integer,
    MetaData,
    Table,
    Text,
    and_,
    cast,
    distinct,
    func,
    insert,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.enums import EventType, VerificationLevel, Visibility
from app.ingestion.base import NormalizedRecord, RawSnapshot
from app.ingestion.hcad.normalize import (
    HCAD_PARSER_VERSION,
    HCAD_RECORD_TYPE,
    HCAD_SOURCE_NAME,
    OWNERSHIP_TRANSFER_TITLE,
    HCADRowError,
    ParsedProperty,
    parse_new_owner_date,
    parse_property_fields,
)
from app.ingestion.hcad.parse import ParseStats, open_real_acct, parse_rows
from app.models import LedgerEvent, Property, PropertyAddress, SourceRecord

logger = logging.getLogger(__name__)

# Staging lives outside Base.metadata on purpose: it is a runtime working
# table, not part of the migrated canonical schema. One row per parseable
# snapshot row, carrying BOTH the validated property field values and the raw
# provenance payload, so every downstream step is a set-based statement.
staging_metadata = MetaData()

hcad_staging = Table(
    "hcad_staging",
    staging_metadata,
    Column("seq", BigInteger, Identity(always=True), primary_key=True),
    Column("acct", Text),  # stripped HCAD account id (never blank)
    Column("valid", Boolean),  # passed property field validation
    # Canonical property columns (NULL when valid = false):
    Column("address_line1", Text),
    Column("unit", Text),
    Column("city", Text),
    Column("state", Text),
    Column("postal_code", Text),
    Column("raw_address", Text),
    Column("normalized_address", Text),
    Column("address_hash", Text),
    Column("year_built", Integer),
    Column("building_sqft", Integer),
    Column("lot_sqft", BigInteger),
    Column("property_type", Text),
    # Raw provenance (always present):
    Column("raw_payload", Text),  # canonical JSON (sorted keys) of the raw row
    Column("content_hash", Text),  # sha256 of raw_payload
    # Ownership transfer date parsed from new_own_dt (NULL if blank/malformed):
    Column("event_date", DateTime(timezone=True)),
    prefixes=["UNLOGGED"],
)

_STAGING_COPY_COLUMNS: tuple[str, ...] = tuple(
    c.name for c in hcad_staging.columns if c.name != "seq"
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

_PROPERTY_INSERT_COLUMNS: tuple[str, ...] = (
    "id",
    "hcad_account_id",
    *_UPDATABLE_COLUMNS,
)


@dataclass
class StageStats:
    """Counters accumulated during the single streaming COPY pass."""

    parse: ParseStats = field(default_factory=ParseStats)
    rows_valid: int = 0
    rows_rejected: int = 0
    ownership_dates_blank: int = 0
    ownership_dates_invalid: int = 0


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


def _affected_rows(result: object) -> int:
    """rowcount of a DML statement.

    ``Session.execute`` is typed ``Result[Any]``, but DML always yields a
    :class:`CursorResult` at runtime — the only object carrying ``rowcount``.
    """
    assert isinstance(result, CursorResult)
    return result.rowcount


def _canonical_payload(row: dict[str, str]) -> tuple[str, str]:
    """Canonical JSON text + sha256 for a raw row.

    MUST stay byte-identical to ``content_hash_for_payload`` in
    app/ingestion/runner.py (sha256 of ``json.dumps(payload, sort_keys=True)``)
    — re-imports dedupe source_records on this hash.
    """
    canonical = json.dumps(row, sort_keys=True)
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def _staging_row(
    parsed: ParsedProperty | None,
    *,
    acct: str,
    canonical: str | None,
    content_hash: str | None,
    event_date: object,
) -> tuple[object, ...]:
    if parsed is None:
        # 12 NULL property columns: address_line1 .. property_type (see table).
        return (acct, False, *(None,) * 12, canonical, content_hash, event_date)
    return (
        parsed.hcad_account_id,
        True,
        parsed.address_line1,
        parsed.unit,
        parsed.city,
        parsed.state,
        parsed.postal_code,
        parsed.raw_address,
        parsed.normalized_address,
        parsed.address_hash,
        parsed.year_built,
        parsed.building_sqft,
        parsed.lot_sqft,
        parsed.property_type,
        canonical,
        content_hash,
        event_date,
    )


def _copy_into_staging(conn: Connection) -> psycopg.Connection:
    driver = conn.connection.driver_connection
    assert isinstance(driver, psycopg.Connection), "hcad staging COPY requires psycopg"
    return driver


_COPY_SQL = (
    "COPY hcad_staging ("
    + ", ".join(f'"{name}"' for name in _STAGING_COPY_COLUMNS)
    + ") FROM STDIN"
)


def load_staging(conn: Connection, file: Path) -> StageStats:
    """One streaming pass: parse, validate, hash, and COPY into hcad_staging.

    Rows are handled one line at a time and written straight into the COPY
    protocol — the file is never loaded into memory. Field-validation
    failures stage ``valid = false`` rows (raw provenance is never lost);
    blank/malformed ``new_own_dt`` values stage a NULL event_date and are
    counted, never fatal.
    """
    ensure_staging(conn)
    stats = StageStats()
    driver = _copy_into_staging(conn)
    with open_real_acct(file) as fh, driver.cursor() as cur, cur.copy(_COPY_SQL) as copy:
        for row in parse_rows(fh, stats=stats.parse):
            canonical, digest = _canonical_payload(row)
            own_raw = (row.get("new_own_dt") or "").strip()
            event_date = parse_new_owner_date(own_raw)
            if not own_raw:
                stats.ownership_dates_blank += 1
            elif event_date is None:
                stats.ownership_dates_invalid += 1
            parsed = _parse_or_none(dict(row))
            if parsed is None:
                stats.rows_rejected += 1
            else:
                stats.rows_valid += 1
            copy.write_row(
                _staging_row(
                    parsed,
                    acct=row["acct"].strip(),
                    canonical=canonical,
                    content_hash=digest,
                    event_date=event_date,
                )
            )
    logger.info(
        "hcad staging loaded",
        extra={
            "source_name": HCAD_SOURCE_NAME,
            "rows_copied": stats.parse.rows_yielded,
            "rows_malformed": stats.parse.rows_skipped,
            "rows_valid": stats.rows_valid,
            "rows_rejected": stats.rows_rejected,
            "ownership_dates_blank": stats.ownership_dates_blank,
            "ownership_dates_invalid": stats.ownership_dates_invalid,
        },
    )
    return stats


def upsert_staged_properties(session: Session) -> UpsertStats:
    """Set-based property UPSERT + alias insert from hcad_staging.

    A duplicate account's LAST file occurrence wins (window function over
    ``seq``). Counters are derived from statement rowcounts and property
    counts, so no RETURNING result set is ever materialized at scale.
    """
    stats = UpsertStats()
    stats.rows_valid, stats.rows_rejected, distinct_accounts = session.execute(
        select(
            func.count().filter(hcad_staging.c.valid),
            func.count().filter(~hcad_staging.c.valid),
            func.count(distinct(hcad_staging.c.acct)).filter(hcad_staging.c.valid),
        )
    ).one()

    rn = (
        func.row_number()
        .over(partition_by=hcad_staging.c.acct, order_by=hcad_staging.c.seq.desc())
        .label("rn")
    )
    ranked = (
        select(
            hcad_staging.c.acct,
            *(hcad_staging.c[name] for name in _UPDATABLE_COLUMNS),
            rn,
        )
        .where(hcad_staging.c.valid)
        .subquery("hcad_ranked")
    )
    last_rows = select(
        func.gen_random_uuid(),
        ranked.c.acct,
        *(ranked.c[name] for name in _UPDATABLE_COLUMNS),
    ).where(ranked.c.rn == 1)

    stmt = pg_insert(Property).from_select(list(_PROPERTY_INSERT_COLUMNS), last_rows)
    excluded = stmt.excluded
    changed = or_(
        *(
            getattr(Property, name).is_distinct_from(getattr(excluded, name))
            for name in _UPDATABLE_COLUMNS
        )
    )
    set_: dict[str, object] = {
        name: getattr(excluded, name) for name in _UPDATABLE_COLUMNS
    }
    set_["updated_at"] = func.now()
    upsert = stmt.on_conflict_do_update(
        index_elements=[Property.hcad_account_id], set_=set_, where=changed
    )

    count_stmt = select(func.count()).select_from(Property)
    before = session.execute(count_stmt).scalar_one()
    # preserve_rowcount: SQLAlchemy only exposes rowcount for UPDATE/DELETE by
    # default; with it, INSERT .. ON CONFLICT reports inserted+actually-updated
    # rows without materializing a RETURNING result set at 1.5M-row scale.
    touched = _affected_rows(
        session.execute(upsert.execution_options(preserve_rowcount=True))
    )
    after = session.execute(count_stmt).scalar_one()
    stats.properties_inserted = after - before
    stats.properties_updated = touched - stats.properties_inserted
    stats.properties_unchanged = distinct_accounts - touched
    stats.aliases_inserted = _insert_aliases(session)
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


def _insert_aliases(session: Session) -> int:
    """Insert property_addresses alias rows (source 'hcad') not already present.

    One ``INSERT ... SELECT`` for the whole staging table; presence is keyed
    on (property_id, normalized_address) so re-imports add nothing. The FIRST
    file occurrence of each (acct, normalized_address) supplies raw_address.
    """
    rn = (
        func.row_number()
        .over(
            partition_by=(hcad_staging.c.acct, hcad_staging.c.normalized_address),
            order_by=hcad_staging.c.seq,
        )
        .label("rn")
    )
    ranked = (
        select(
            hcad_staging.c.acct,
            hcad_staging.c.raw_address,
            hcad_staging.c.normalized_address,
            rn,
        )
        .where(hcad_staging.c.valid)
        .subquery("hcad_alias_ranked")
    )
    already_present = (
        select(PropertyAddress.id)
        .where(
            PropertyAddress.property_id == Property.id,
            PropertyAddress.normalized_address == ranked.c.normalized_address,
        )
        .exists()
    )
    selectable = (
        select(
            func.gen_random_uuid(),
            Property.id,
            ranked.c.raw_address,
            ranked.c.normalized_address,
            literal(HCAD_SOURCE_NAME),
        )
        .join_from(ranked, Property, Property.hcad_account_id == ranked.c.acct)
        .where(ranked.c.rn == 1, ~already_present)
    )
    result = session.execute(
        insert(PropertyAddress)
        .from_select(
            ["id", "property_id", "raw_address", "normalized_address", "source"],
            selectable,
        )
        .execution_options(preserve_rowcount=True)
    )
    return _affected_rows(result)


def upsert_properties(session: Session, records: Iterable[NormalizedRecord]) -> UpsertStats:
    """Staged set-based UPSERT for already-normalized records (no snapshot file).

    ``records`` are :class:`NormalizedRecord` items whose ``raw_payload``
    carries the real_acct fields; each is re-validated (rejects counted).
    Properties and aliases only — no source_records, no ledger events.
    """
    conn = session.connection()
    ensure_staging(conn)
    driver = _copy_into_staging(conn)
    with driver.cursor() as cur, cur.copy(_COPY_SQL) as copy:
        for rec in records:
            raw = dict(rec.raw_payload)
            parsed = _parse_or_none(raw)
            acct = str(raw.get("acct", "")).strip() or rec.source_record_id
            copy.write_row(
                _staging_row(
                    parsed, acct=acct, canonical=None, content_hash=None, event_date=None
                )
            )
    return upsert_staged_properties(session)


def insert_source_records(session: Session, snapshot: RawSnapshot) -> int:
    """Bulk-insert raw per-row provenance records from staging, deduped.

    Every parseable row (including rows rejected by field validation) is
    retained exactly as received — provenance is never lost. One ``INSERT ...
    SELECT ... WHERE NOT EXISTS`` on the ``(source_name, source_record_id,
    content_hash)`` unique triple, with ``ON CONFLICT DO NOTHING`` as a
    backstop; returns the number of rows actually created.
    """
    rn = (
        func.row_number()
        .over(
            partition_by=(hcad_staging.c.acct, hcad_staging.c.content_hash),
            order_by=hcad_staging.c.seq,
        )
        .label("rn")
    )
    ranked = select(
        hcad_staging.c.acct, hcad_staging.c.raw_payload, hcad_staging.c.content_hash, rn
    ).subquery("hcad_src_ranked")
    already_present = (
        select(SourceRecord.id)
        .where(
            SourceRecord.source_name == HCAD_SOURCE_NAME,
            SourceRecord.source_record_id == ranked.c.acct,
            SourceRecord.content_hash == ranked.c.content_hash,
        )
        .exists()
    )
    selectable = select(
        func.gen_random_uuid(),
        literal(HCAD_SOURCE_NAME),
        ranked.c.acct,
        literal(HCAD_RECORD_TYPE),
        cast(ranked.c.raw_payload, JSONB),
        literal(snapshot.source_url, Text),
        literal(snapshot.retrieved_at, DateTime(timezone=True)),
        ranked.c.content_hash,
        literal(HCAD_PARSER_VERSION),
    ).where(ranked.c.rn == 1, ~already_present)
    stmt = (
        pg_insert(SourceRecord)
        .from_select(
            [
                "id",
                "source_name",
                "source_record_id",
                "record_type",
                "raw_payload",
                "source_url",
                "retrieved_at",
                "content_hash",
                "parser_version",
            ],
            selectable,
        )
        .on_conflict_do_nothing(
            index_elements=[
                SourceRecord.source_name,
                SourceRecord.source_record_id,
                SourceRecord.content_hash,
            ]
        )
        .execution_options(preserve_rowcount=True)
    )
    created = _affected_rows(session.execute(stmt))
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
    )
    return _affected_rows(session.execute(stmt))


def create_ownership_events(session: Session) -> int:
    """Create OWNERSHIP_TRANSFER ledger events from staged new_own_dt values.

    One event per (property, transfer date): event_date is the UTC-midnight
    ``new_own_dt``, title is the fixed public wording, summary is None (owner
    names / mailing fields NEVER appear — privacy spec §37), verification is
    GOVERNMENT_RECORD with confidence 1.0, and provenance links the staged
    row's own hcad source_records version.

    Logical dedup: the insert skips any (property_id, event_date) that already
    carries an OWNERSHIP_TRANSFER event FROM ANY SOURCE — so re-imports create
    nothing, while a changed new_own_dt (a real subsequent sale) creates a new
    event for the new date and retains the old one. Only valid rows (rows that
    imported a property) emit events; retracted events still block re-creation
    because history is never deleted.
    """
    rn = (
        func.row_number()
        .over(
            partition_by=(hcad_staging.c.acct, hcad_staging.c.event_date),
            order_by=hcad_staging.c.seq.desc(),
        )
        .label("rn")
    )
    ranked = (
        select(
            hcad_staging.c.acct, hcad_staging.c.content_hash, hcad_staging.c.event_date, rn
        )
        .where(hcad_staging.c.valid, hcad_staging.c.event_date.is_not(None))
        .subquery("hcad_own_ranked")
    )
    already_present = (
        select(LedgerEvent.id)
        .where(
            LedgerEvent.property_id == Property.id,
            LedgerEvent.event_type == literal(EventType.OWNERSHIP_TRANSFER.value),
            LedgerEvent.event_date == ranked.c.event_date,
        )
        .exists()
    )
    selectable = (
        select(
            func.gen_random_uuid(),
            Property.id,
            literal(EventType.OWNERSHIP_TRANSFER.value),
            ranked.c.event_date,
            literal(OWNERSHIP_TRANSFER_TITLE),
            SourceRecord.id,
            literal(VerificationLevel.GOVERNMENT_RECORD.value),
            literal(1.0),
            literal(Visibility.PUBLIC.value),
        )
        .join_from(ranked, Property, Property.hcad_account_id == ranked.c.acct)
        .join(
            SourceRecord,
            and_(
                SourceRecord.source_name == HCAD_SOURCE_NAME,
                SourceRecord.source_record_id == ranked.c.acct,
                SourceRecord.content_hash == ranked.c.content_hash,
            ),
        )
        .where(ranked.c.rn == 1, ~already_present)
    )
    stmt = (
        insert(LedgerEvent)
        .from_select(
            [
                "id",
                "property_id",
                "event_type",
                "event_date",
                "title",
                "source_record_id",
                "verification_level",
                "confidence",
                "visibility",
            ],
            selectable,
        )
        .execution_options(preserve_rowcount=True)
    )
    created = _affected_rows(session.execute(stmt))
    logger.info(
        "hcad ownership events created",
        extra={"source_name": HCAD_SOURCE_NAME, "ownership_events_created": created},
    )
    return created
