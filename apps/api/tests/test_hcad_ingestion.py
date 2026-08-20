"""HCAD adapter tests: streaming parse, normalization, and the bootstrap import.

Fixture facts (data/fixtures/hcad_sample/real_acct.txt, synthetic rows):
- 25 data rows, tab-delimited, real_acct header layout incl. new_own_dt;
- acct 0450230000012 appears twice with im_sq_ft 2450 then 2610 (last wins);
  both occurrences carry new_own_dt 06/14/2018 -> ONE ownership event;
- acct 0250470000032 has garbage im_sq_ft ("ABC123") -> rejected (its valid
  new_own_dt creates no event: the row never imported a property);
- acct 0450610000029 has empty yr_impr -> year_built None;
- acct 1150330000067 has empty site_addr_2 -> city falls back to HOUSTON;
- accts 1024470000015/16, 1180940000102, 0410250000019/20 carry unit numbers;
- new_own_dt: 16 distinct valid accounts carry a parseable date, 6 rows are
  blank, 1 is malformed (0932640000054, "02/30/2020" — impossible date);
=> 23 distinct properties, 23 hcad aliases, 25 raw source records,
   16 OWNERSHIP_TRANSFER ledger events.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session

from app.enums import EventType, VerificationLevel, Visibility
from app.ingestion.base import RawSnapshot
from app.ingestion.hcad.download import snapshot_from_file
from app.ingestion.hcad.normalize import (
    OWNERSHIP_TRANSFER_TITLE,
    HCADAdapter,
    HCADRowError,
    parse_new_owner_date,
    parse_property_fields,
)
from app.ingestion.hcad.parse import HCADParseError, ParseStats, parse_rows
from app.ingestion.hcad.sync import run_hcad_sync
from app.models import LedgerEvent, Property, PropertyAddress, SourceRecord

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "hcad_sample"
    / "real_acct.txt"
)

TOTAL_ROWS = 25
REJECTED_ROWS = 1
VALID_ROWS = TOTAL_ROWS - REJECTED_ROWS  # 24
DISTINCT_PROPERTIES = 23  # 24 valid rows, one duplicated account
OWNERSHIP_EVENTS = 16  # distinct valid accounts with a parseable new_own_dt

DUP_ACCT = "0450230000012"
DUP_FINAL_SQFT = 2610
DUP_TRANSFER_DATE = datetime(2018, 6, 14, tzinfo=UTC)
MISSING_YR_ACCT = "0450610000029"
REJECT_ACCT = "0250470000032"
EMPTY_CITY_ACCT = "1150330000067"
UNIT_ACCT = "1024470000015"
UPDATE_ACCT = "0661040000102"  # 815 HEIGHTS BLVD, im_sq_ft 1890 in the fixture
UPDATE_ACCT_TRANSFER_DATE = datetime(2021, 5, 17, tzinfo=UTC)
KIRBY_ACCT = "0330190000041"
KIRBY_TRANSFER_DATE = datetime(2012, 9, 28, tzinfo=UTC)
MALFORMED_DATE_ACCT = "0932640000054"  # new_own_dt "02/30/2020" -> no event
BLANK_DATE_ACCT = "0891220000078"  # new_own_dt blank -> no event

IM_SQ_FT_COLUMN_INDEX = 10  # 0-based position of im_sq_ft in the fixture layout
NEW_OWN_DT_COLUMN_INDEX = 13  # 0-based position of new_own_dt (appended last)


@pytest.fixture()
def sync_session_factory(db: Session) -> Callable[[], Session]:
    """Session factory sharing the test connection, so sync commits become savepoints."""
    bind = db.get_bind()
    assert isinstance(bind, Connection)

    def _factory() -> Session:
        return Session(
            bind=bind,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    return _factory


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "real_acct.txt"
    target.write_bytes(FIXTURE.read_bytes())
    return target


def _import(factory: Callable[[], Session], file: Path, tmp_path: Path) -> object:
    return run_hcad_sync(factory, file=file, raw_dir=tmp_path / "raw")


def _sample_row(**overrides: str) -> dict[str, str]:
    row = {
        "acct": "0450230000012",
        "str_num": "1234",
        "str": "Westheimer",
        "str_sfx": "Rd",
        "str_unit": "",
        "site_addr_1": "1234 WESTHEIMER RD",
        "site_addr_2": "HOUSTON",
        "site_addr_3": "77006",
        "state_class": "A1",
        "yr_impr": "1955",
        "im_sq_ft": "2450",
        "land_ar": "6500",
        "tot_mkt_val": "385000",
        "new_own_dt": "06/14/2018",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# parse.py — streaming
# ---------------------------------------------------------------------------


def test_parse_rows_returns_lazy_iterator_over_fixture() -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines(keepends=True)
    consumed = 0

    def counting_lines() -> Iterator[str]:
        nonlocal consumed
        for line in lines:
            consumed += 1
            yield line

    rows = parse_rows(counting_lines())
    assert isinstance(rows, Iterator)
    assert not isinstance(rows, list)

    first = next(rows)
    # Streaming: only the header and the first data row have been read.
    assert consumed <= 3
    assert consumed < len(lines)
    assert first["acct"] == DUP_ACCT
    assert first["str"] == "WESTHEIMER"

    remaining = sum(1 for _ in rows)
    assert 1 + remaining == TOTAL_ROWS


def test_parse_rows_skips_and_counts_malformed_rows() -> None:
    header = (
        "acct\tstr_num\tstr\tstr_sfx\tstr_unit\tsite_addr_1\tsite_addr_2"
        "\tsite_addr_3\tstate_class\tyr_impr\tim_sq_ft\tland_ar\ttot_mkt_val"
    )
    good = "0450230000099\t1\tMAIN\tST\t\t1 MAIN ST\tHOUSTON\t77002\tA1\t1950\t1000\t5000\t100000"
    short = "0450230000098\t2\tMAIN"  # too few fields
    extra = good.replace("0450230000099", "0450230000097") + "\tEXTRA"  # too many fields
    no_acct = "\t3\tMAIN\tST\t\t3 MAIN ST\tHOUSTON\t77002\tA1\t1950\t1000\t5000\t100000"
    content = "\n".join([header, good, short, extra, no_acct]) + "\n"

    stats = ParseStats()
    rows = list(parse_rows(iter(content.splitlines(keepends=True)), stats=stats))

    assert [row["acct"] for row in rows] == ["0450230000099"]
    assert stats.rows_yielded == 1
    assert stats.rows_skipped == 3
    assert stats.rows_read == 4


def test_parse_rows_rejects_missing_header_columns() -> None:
    content = "acct\tstr_num\n0450230000099\t1\n"
    with pytest.raises(HCADParseError, match="missing required columns"):
        next(parse_rows(iter(content.splitlines(keepends=True))))


def test_adapter_parse_streams_snapshot(tmp_path: Path) -> None:
    file = _copy_fixture(tmp_path)
    snapshot = snapshot_from_file(file)
    assert isinstance(snapshot, RawSnapshot)
    assert snapshot.checksum == hashlib.sha256(file.read_bytes()).hexdigest()

    rows = HCADAdapter().parse(snapshot)
    assert isinstance(rows, Iterator)
    assert not isinstance(rows, list)
    assert sum(1 for _ in rows) == TOTAL_ROWS


# ---------------------------------------------------------------------------
# normalize.py — field mapping
# ---------------------------------------------------------------------------


def test_normalize_maps_fields_and_normalizes_address() -> None:
    record = HCADAdapter().normalize(_sample_row())

    assert record.record_type == "real_acct"
    assert record.source_record_id == DUP_ACCT
    assert record.hcad_account_id == DUP_ACCT
    assert record.raw_address == "1234 Westheimer Rd"
    assert record.normalized_address == "1234 WESTHEIMER RD"
    assert record.event_candidates == []  # property bootstrap: no ledger events
    assert record.raw_payload["im_sq_ft"] == "2450"

    fields = parse_property_fields(record.raw_payload)
    assert fields.address_line1 == "1234 Westheimer Rd"
    assert fields.unit is None
    assert fields.city == "HOUSTON"
    assert fields.state == "TX"
    assert fields.postal_code == "77006"
    assert fields.year_built == 1955
    assert fields.building_sqft == 2450
    assert fields.lot_sqft == 6500
    assert fields.property_type == "A1"
    assert fields.address_hash == hashlib.sha256(b"1234 WESTHEIMER RD").hexdigest()


def test_normalize_handles_units_missing_year_and_city_fallback() -> None:
    fields = parse_property_fields(
        _sample_row(str_unit="4B", yr_impr="", site_addr_2="")
    )
    assert fields.unit == "4B"
    assert fields.raw_address == "1234 Westheimer Rd UNIT 4B"
    assert fields.normalized_address == "1234 WESTHEIMER RD APT 4B"
    assert fields.year_built is None
    assert fields.city == "HOUSTON"


def test_normalize_rejects_garbage_and_negative_sqft() -> None:
    with pytest.raises(HCADRowError, match="im_sq_ft"):
        HCADAdapter().normalize(_sample_row(im_sq_ft="ABC123"))
    with pytest.raises(HCADRowError, match="negative"):
        parse_property_fields(_sample_row(im_sq_ft="-50"))
    with pytest.raises(HCADRowError, match="acct"):
        parse_property_fields(_sample_row(acct=""))


# ---------------------------------------------------------------------------
# sync pipeline — bootstrap import against the fixture
# ---------------------------------------------------------------------------


def test_import_bootstraps_properties_with_provenance(
    db: Session, sync_session_factory: Callable[[], Session], tmp_path: Path
) -> None:
    file = _copy_fixture(tmp_path)
    run = run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")

    # --- run metrics (counter mapping documented in sync.py) ---
    assert run.status == "SUCCEEDED"
    assert run.error_message is None
    assert run.finished_at is not None
    assert run.records_parsed == TOTAL_ROWS
    assert run.records_matched == VALID_ROWS
    assert run.records_rejected == REJECTED_ROWS  # reject case counted, not crashed
    assert run.records_unmatched == 0
    assert run.source_records_created == TOTAL_ROWS
    assert run.events_created == OWNERSHIP_EVENTS
    assert run.parser_version == "1.2.0"

    # --- snapshot archived + checksummed ---
    expected_checksum = hashlib.sha256(file.read_bytes()).hexdigest()
    assert run.snapshot_checksum == expected_checksum
    archived = Path(run.snapshot_path)
    assert archived.is_file()
    assert archived.parent == tmp_path / "raw"
    assert archived.read_bytes() == file.read_bytes()

    # --- properties created, keyed uniquely on hcad_account_id ---
    assert db.execute(select(func.count()).select_from(Property)).scalar_one() == (
        DISTINCT_PROPERTIES
    )
    duplicate_accounts = db.execute(
        select(Property.hcad_account_id)
        .group_by(Property.hcad_account_id)
        .having(func.count() > 1)
    ).all()
    assert duplicate_accounts == []

    def prop(acct: str) -> Property:
        return db.execute(
            select(Property).where(Property.hcad_account_id == acct)
        ).scalar_one()

    # In-file duplicate account: the LAST occurrence's sqft wins.
    assert prop(DUP_ACCT).building_sqft == DUP_FINAL_SQFT

    # Field mapping on a mixed-case row.
    kirby = prop("0330190000041")
    assert kirby.address_line1 == "2205 Kirby Dr"
    assert kirby.normalized_address == "2205 KIRBY DR"
    assert kirby.city == "HOUSTON"
    assert kirby.state == "TX"
    assert kirby.postal_code == "77019"
    assert kirby.year_built == 1948
    assert kirby.building_sqft == 3120
    assert kirby.lot_sqft == 8750
    assert kirby.property_type == "A1"

    # Missing yr_impr -> None; empty site_addr_2 -> HOUSTON fallback; unit rows.
    assert prop(MISSING_YR_ACCT).year_built is None
    assert prop(EMPTY_CITY_ACCT).city == "HOUSTON"
    unit_prop = prop(UNIT_ACCT)
    assert unit_prop.unit == "3"
    assert unit_prop.normalized_address == "4210 MONTROSE BLVD APT 3"

    # Rejected row never became a property.
    rejected = db.execute(
        select(Property).where(Property.hcad_account_id == REJECT_ACCT)
    ).scalar_one_or_none()
    assert rejected is None

    # --- aliases (source 'hcad') ---
    aliases = (
        db.execute(select(PropertyAddress).where(PropertyAddress.source == "hcad"))
        .scalars()
        .all()
    )
    assert len(aliases) == DISTINCT_PROPERTIES
    kirby_alias = next(a for a in aliases if a.property_id == kirby.id)
    assert kirby_alias.raw_address == "2205 Kirby Dr"
    assert kirby_alias.normalized_address == "2205 KIRBY DR"

    # --- raw source-record provenance for every imported property ---
    source_records = (
        db.execute(select(SourceRecord).where(SourceRecord.source_name == "hcad"))
        .scalars()
        .all()
    )
    assert len(source_records) == TOTAL_ROWS
    assert all(sr.record_type == "real_acct" for sr in source_records)
    assert all(sr.raw_payload["acct"] == sr.source_record_id for sr in source_records)
    properties_without_provenance = db.execute(
        select(func.count())
        .select_from(Property)
        .where(
            ~select(SourceRecord.id)
            .where(
                SourceRecord.property_id == Property.id,
                SourceRecord.source_name == "hcad",
            )
            .exists()
        )
    ).scalar_one()
    assert properties_without_provenance == 0
    # The rejected row keeps raw provenance but stays unlinked.
    reject_records = [
        sr for sr in source_records if sr.source_record_id == REJECT_ACCT
    ]
    assert len(reject_records) == 1
    assert reject_records[0].property_id is None

    # --- OWNERSHIP_TRANSFER events from new_own_dt, with provenance ---
    events = db.execute(select(LedgerEvent)).scalars().all()
    assert len(events) == OWNERSHIP_EVENTS
    src_by_id = {sr.id: sr for sr in source_records}
    for event in events:
        assert event.event_type == EventType.OWNERSHIP_TRANSFER
        assert event.title == OWNERSHIP_TRANSFER_TITLE
        assert event.summary is None  # never owner names / mailing fields
        assert event.verification_level == VerificationLevel.GOVERNMENT_RECORD
        assert event.confidence == 1.0
        assert event.visibility == Visibility.PUBLIC
        assert event.retracted_at is None
        # Provenance: every government event links its own hcad source record,
        # and that record belongs to the same property.
        assert event.source_record_id is not None
        provenance = src_by_id[event.source_record_id]
        assert provenance.source_name == "hcad"
        assert provenance.property_id == event.property_id

    # One event per distinct (property, transfer date).
    assert len({(e.property_id, e.event_date) for e in events}) == OWNERSHIP_EVENTS

    def events_for(acct: str) -> list[LedgerEvent]:
        return [e for e in events if e.property_id == prop(acct).id]

    kirby_events = events_for(KIRBY_ACCT)
    assert [e.event_date for e in kirby_events] == [KIRBY_TRANSFER_DATE]

    # The duplicated account shares one date across both rows -> ONE event,
    # linked to the row that won the upsert (the last occurrence, sqft 2610).
    dup_events = events_for(DUP_ACCT)
    assert [e.event_date for e in dup_events] == [DUP_TRANSFER_DATE]
    assert src_by_id[dup_events[0].source_record_id].raw_payload["im_sq_ft"] == "2610"

    # Blank and malformed new_own_dt: the rows imported, but no event exists.
    assert prop(MALFORMED_DATE_ACCT).building_sqft == 1820  # row still imported
    assert events_for(MALFORMED_DATE_ACCT) == []
    assert events_for(BLANK_DATE_ACCT) == []
    # The rejected row's valid date never became an event (no property).
    assert all(
        src_by_id[e.source_record_id].source_record_id != REJECT_ACCT for e in events
    )


def test_reimport_is_idempotent(
    db: Session, sync_session_factory: Callable[[], Session], tmp_path: Path
) -> None:
    file = _copy_fixture(tmp_path)
    run1 = run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")
    run2 = run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")

    assert run1.source_records_created == TOTAL_ROWS
    assert run1.events_created == OWNERSHIP_EVENTS
    assert run2.status == "SUCCEEDED"
    assert run2.records_parsed == TOTAL_ROWS
    assert run2.records_matched == VALID_ROWS
    assert run2.records_rejected == REJECTED_ROWS
    assert run2.source_records_created == 0  # zero new source_records
    assert run2.events_created == 0  # zero new ledger events

    # Zero new properties, aliases, raw records, or ledger events.
    assert db.execute(select(func.count()).select_from(Property)).scalar_one() == (
        DISTINCT_PROPERTIES
    )
    assert db.execute(
        select(func.count())
        .select_from(SourceRecord)
        .where(SourceRecord.source_name == "hcad")
    ).scalar_one() == TOTAL_ROWS
    assert db.execute(
        select(func.count())
        .select_from(PropertyAddress)
        .where(PropertyAddress.source == "hcad")
    ).scalar_one() == DISTINCT_PROPERTIES
    assert db.execute(
        select(func.count()).select_from(LedgerEvent)
    ).scalar_one() == OWNERSHIP_EVENTS


def test_changed_row_updates_property_without_new_row(
    db: Session, sync_session_factory: Callable[[], Session], tmp_path: Path
) -> None:
    file = _copy_fixture(tmp_path)
    run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")
    original = db.execute(
        select(Property).where(Property.hcad_account_id == UPDATE_ACCT)
    ).scalar_one()
    assert original.building_sqft == 1890

    # HCAD publishes a new export where this account's improvement grew.
    lines = file.read_text(encoding="utf-8").splitlines()
    changed = 0
    for i, line in enumerate(lines):
        fields = line.split("\t")
        if fields[0] == UPDATE_ACCT:
            fields[IM_SQ_FT_COLUMN_INDEX] = "2050"
            lines[i] = "\t".join(fields)
            changed += 1
    assert changed == 1
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    run2 = run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")
    assert run2.status == "SUCCEEDED"

    db.expire_all()
    updated = db.execute(
        select(Property).where(Property.hcad_account_id == UPDATE_ACCT)
    ).scalar_one()
    assert updated.id == original.id  # updated in place, no new row
    assert updated.building_sqft == 2050
    assert db.execute(select(func.count()).select_from(Property)).scalar_one() == (
        DISTINCT_PROPERTIES
    )

    # The changed row is new content -> exactly one extra provenance record.
    assert run2.source_records_created == 1
    account_records = db.execute(
        select(func.count())
        .select_from(SourceRecord)
        .where(
            SourceRecord.source_name == "hcad",
            SourceRecord.source_record_id == UPDATE_ACCT,
        )
    ).scalar_one()
    assert account_records == 2

    # Same new_own_dt in the changed row -> zero new ownership events.
    assert run2.events_created == 0
    assert db.execute(
        select(func.count()).select_from(LedgerEvent)
    ).scalar_one() == OWNERSHIP_EVENTS


# ---------------------------------------------------------------------------
# OWNERSHIP_TRANSFER events from new_own_dt
# ---------------------------------------------------------------------------


def test_parse_new_owner_date() -> None:
    # Valid MM/DD/YYYY (real exports left-pad with spaces) -> UTC midnight.
    assert parse_new_owner_date("06/14/2018") == datetime(2018, 6, 14, tzinfo=UTC)
    assert parse_new_owner_date("  12/05/2019") == datetime(2019, 12, 5, tzinfo=UTC)
    assert parse_new_owner_date("3/8/2019") == datetime(2019, 3, 8, tzinfo=UTC)
    # Blank / malformed -> None, never an exception.
    assert parse_new_owner_date("") is None
    assert parse_new_owner_date("   ") is None
    assert parse_new_owner_date(None) is None
    assert parse_new_owner_date("02/30/2020") is None  # impossible calendar date
    assert parse_new_owner_date("13/07/2019") is None  # month 13
    assert parse_new_owner_date("06/14/18") is None  # 2-digit year
    assert parse_new_owner_date("2019-12-05") is None
    assert parse_new_owner_date("N/A") is None


def _set_new_own_dt(file: Path, acct: str, value: str) -> None:
    """Rewrite the acct's new_own_dt column in a fixture copy (all occurrences)."""
    lines = file.read_text(encoding="utf-8").splitlines()
    changed = 0
    for i, line in enumerate(lines[1:], start=1):
        fields = line.split("\t")
        if fields[0] == acct:
            fields[NEW_OWN_DT_COLUMN_INDEX] = value
            lines[i] = "\t".join(fields)
            changed += 1
    assert changed >= 1
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ownership_events(db: Session, acct: str) -> list[LedgerEvent]:
    return list(
        db.execute(
            select(LedgerEvent)
            .join(Property, LedgerEvent.property_id == Property.id)
            .where(
                Property.hcad_account_id == acct,
                LedgerEvent.event_type == EventType.OWNERSHIP_TRANSFER,
            )
            .order_by(LedgerEvent.event_date)
        )
        .scalars()
        .all()
    )


def test_changed_new_own_dt_creates_second_event_and_retains_first(
    db: Session, sync_session_factory: Callable[[], Session], tmp_path: Path
) -> None:
    file = _copy_fixture(tmp_path)
    run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")
    first = _ownership_events(db, UPDATE_ACCT)
    assert [e.event_date for e in first] == [UPDATE_ACCT_TRANSFER_DATE]

    # HCAD publishes a new export: the property sold again on a later date.
    _set_new_own_dt(file, UPDATE_ACCT, "03/09/2024")
    run2 = run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")
    assert run2.status == "SUCCEEDED"
    assert run2.events_created == 1

    db.expire_all()
    events = _ownership_events(db, UPDATE_ACCT)
    assert [e.event_date for e in events] == [
        UPDATE_ACCT_TRANSFER_DATE,  # the old event is retained
        datetime(2024, 3, 9, tzinfo=UTC),  # the new sale is a new event
    ]
    assert events[0].id == first[0].id
    # The new event's provenance is the new content version of the row.
    new_record = db.get(SourceRecord, events[1].source_record_id)
    assert new_record is not None
    assert new_record.source_record_id == UPDATE_ACCT
    assert new_record.raw_payload["new_own_dt"] == "03/09/2024"
    # Total: the fixture's events plus exactly the one new sale.
    assert db.execute(
        select(func.count()).select_from(LedgerEvent)
    ).scalar_one() == OWNERSHIP_EVENTS + 1


def test_ownership_event_skipped_when_any_source_already_has_it(
    db: Session, sync_session_factory: Callable[[], Session], tmp_path: Path
) -> None:
    """Dedup is logical on (property, event_date) — ANY source blocks a duplicate."""
    file = _copy_fixture(tmp_path)
    run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")

    prior_date = datetime(2010, 4, 1, tzinfo=UTC)
    blank_prop = db.execute(
        select(Property).where(Property.hcad_account_id == BLANK_DATE_ACCT)
    ).scalar_one()
    # An OWNERSHIP_TRANSFER for the same date already exists from another
    # pipeline (no hcad source record attached).
    db.add(
        LedgerEvent(
            property_id=blank_prop.id,
            event_type=EventType.OWNERSHIP_TRANSFER,
            event_date=prior_date,
            title=OWNERSHIP_TRANSFER_TITLE,
            summary=None,
            verification_level=VerificationLevel.GOVERNMENT_RECORD,
            confidence=1.0,
            visibility=Visibility.PUBLIC,
        )
    )
    db.flush()

    # HCAD now publishes the same transfer date for that account.
    _set_new_own_dt(file, BLANK_DATE_ACCT, "04/01/2010")
    run2 = run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")
    assert run2.status == "SUCCEEDED"
    assert run2.source_records_created == 1  # changed row is new content...
    assert run2.events_created == 0  # ...but the event already exists

    db.expire_all()
    events = _ownership_events(db, BLANK_DATE_ACCT)
    assert [e.event_date for e in events] == [prior_date]
    assert events[0].source_record_id is None  # the pre-existing event, untouched


def test_blank_and_malformed_dates_import_without_events(
    db: Session, sync_session_factory: Callable[[], Session], tmp_path: Path
) -> None:
    file = _copy_fixture(tmp_path)
    run = run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")
    assert run.status == "SUCCEEDED"

    # Rows with blank/malformed new_own_dt still import their properties...
    for acct in (MALFORMED_DATE_ACCT, BLANK_DATE_ACCT):
        assert db.execute(
            select(func.count())
            .select_from(Property)
            .where(Property.hcad_account_id == acct)
        ).scalar_one() == 1
        # ...but never an ownership event.
        assert _ownership_events(db, acct) == []


def test_upsert_properties_from_normalized_records(db: Session) -> None:
    """The no-snapshot upsert path: properties + aliases only, still set-based."""
    from app.ingestion.hcad.load import upsert_properties

    adapter = HCADAdapter()
    records = [
        adapter.normalize(_sample_row()),
        adapter.normalize(_sample_row(acct="0450230000098", str_num="1240")),
    ]
    stats = upsert_properties(db, records)
    assert stats.rows_valid == 2
    assert stats.rows_rejected == 0
    assert stats.properties_inserted == 2
    assert stats.aliases_inserted == 2

    again = upsert_properties(db, records)
    assert again.properties_inserted == 0
    assert again.properties_updated == 0
    assert again.properties_unchanged == 2
    assert again.aliases_inserted == 0
    # No snapshot involved -> no source records, no ledger events.
    assert db.execute(select(func.count()).select_from(LedgerEvent)).scalar_one() == 0
    assert db.execute(
        select(func.count())
        .select_from(SourceRecord)
        .where(SourceRecord.source_name == "hcad")
    ).scalar_one() == 0


def test_no_owner_names_leak_into_public_event_text(
    db: Session, sync_session_factory: Callable[[], Session], tmp_path: Path
) -> None:
    """Owner/mailing fields in the raw layout must never surface in titles/summaries."""
    owner_tokens = ["JANE Q HOMEOWNER", "HOMEOWNER", "JANE", "PO BOX 4521"]
    header = (
        "acct\tstr_num\tstr\tstr_sfx\tstr_unit\tsite_addr_1\tsite_addr_2"
        "\tsite_addr_3\tstate_class\tyr_impr\tim_sq_ft\tland_ar\ttot_mkt_val"
        "\tnew_own_dt\tmailto\tmail_addr_1"
    )
    row = (
        "0450230000099\t1\tMAIN\tST\t\t1 MAIN ST\tHOUSTON\t77002\tA1\t1950"
        "\t1000\t5000\t100000\t06/14/2018\tJANE Q HOMEOWNER\tPO BOX 4521"
    )
    file = tmp_path / "real_acct.txt"
    file.write_text(header + "\n" + row + "\n", encoding="utf-8")

    run = run_hcad_sync(sync_session_factory, file=file, raw_dir=tmp_path / "raw")
    assert run.status == "SUCCEEDED"
    assert run.events_created == 1

    events = db.execute(select(LedgerEvent)).scalars().all()
    assert len(events) == 1
    event = events[0]
    assert event.title == OWNERSHIP_TRANSFER_TITLE
    assert event.summary is None
    for token in owner_tokens:
        assert token not in event.title
        assert token not in (event.summary or "")
    # Provenance retains the raw record privately; the public text never does.
    record = db.get(SourceRecord, event.source_record_id)
    assert record is not None
    assert record.raw_payload["mailto"] == "JANE Q HOMEOWNER"
