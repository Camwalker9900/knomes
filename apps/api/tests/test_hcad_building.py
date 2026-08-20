"""HCAD building-details import tests (beds/baths/quality/remodel year).

Committed fixture (data/fixtures/hcad_sample/building_res_sample.txt —
synthetic rows in the verified 2026 Real_building_land building_res.txt
layout: 31 tab-delimited columns, CRLF, space-padded accts):

- 6 data rows read, 1 garbage row (wrong field count) rejected + counted;
- 0450230000012: qa_cd ``B``, yr_remodel 2016;
- 0330190000041: blank qa_cd, yr_remodel ``0`` -> both columns stay NULL;
- 0661040000102: bld_num 2 (C) then bld_num 1 (B, 2019) -> the primary
  building (smallest bld_num) wins;
- 9999990000001: no bootstrapped property -> counted unmatched, never created.

Room counts live in the zip's fixtures.txt member (type RMB/RMF/RMH), so the
3/2/1-rooms case, the cross-building bedroom sum, an ignored non-room type,
garbage units, and a garbage row are exercised with the inline
``FIXTURES_MEMBER`` content below, written to tmp files in the real layout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.hcad import building
from app.ingestion.hcad.building import (
    KIND_BUILDING_RES,
    KIND_FIXTURES,
    BuildingParseError,
    detect_member_kind,
    import_building_file,
)
from app.lib.address import address_hash, normalize_address
from app.models import Property

BUILDING_RES_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "hcad_sample"
    / "building_res_sample.txt"
)

REMODELED_ACCT = "0450230000012"  # quality B, remodeled 2016, rooms 3/2/1
MISSING_ACCT = "0330190000041"  # blank qa_cd, yr_remodel 0, garbage units row
MULTI_BLD_ACCT = "0661040000102"  # two buildings; primary bld + bedroom sum
UNMATCHED_ACCT = "9999990000001"  # never bootstrapped as a property

FIXTURES_HEADER = "acct\tbld_num\ttype\ttype_dscr\tunits"

# Inline synthetic fixtures.txt member (real layout, CRLF added on write).
FIXTURES_MEMBER = "\r\n".join(
    [
        FIXTURES_HEADER,
        f"{REMODELED_ACCT}\t1\tRMB \tRoom:  Bedroom\t3.00",
        f"{REMODELED_ACCT}\t1\tRMF \tRoom:  Full Bath\t2.00",
        f"{REMODELED_ACCT}\t1\tRMH \tRoom:  Half Bath\t1.00",
        f"{REMODELED_ACCT}\t1\tSTY \tStory Height Index\t2.00",  # ignored type
        f"{MISSING_ACCT}\t1\tRMF \tRoom:  Full Bath\tNaN",  # garbage units -> rejected
        f"{MULTI_BLD_ACCT}\t1\tRMB \tRoom:  Bedroom\t2.00",
        f"{MULTI_BLD_ACCT}\t2\tRMB \tRoom:  Bedroom\t1.00",  # summed across buildings
        "GARBAGE ROW WITH TOO FEW FIELDS",  # rejected + counted
        f"{UNMATCHED_ACCT}\t1\tRMB \tRoom:  Bedroom\t4.00",  # no property
    ]
) + "\r\n"

BUILDING_RES_HEADER = BUILDING_RES_FIXTURE.read_text(encoding="utf-8").splitlines()[0]


def _write_fixtures_member(tmp_path: Path) -> Path:
    target = tmp_path / "fixtures.txt"
    target.write_bytes(FIXTURES_MEMBER.encode("utf-8"))
    return target


def _make_property(acct: str, address: str) -> Property:
    normalized = normalize_address(address)
    return Property(
        hcad_account_id=acct,
        address_line1=address,
        city="HOUSTON",
        state="TX",
        postal_code="77006",
        normalized_address=normalized,
        address_hash=address_hash(normalized),
    )


@pytest.fixture()
def bootstrapped(db: Session) -> dict[str, Property]:
    """Three properties matching the sample accounts (the real_acct bootstrap)."""
    props = {
        REMODELED_ACCT: _make_property(REMODELED_ACCT, "1234 WESTHEIMER RD"),
        MISSING_ACCT: _make_property(MISSING_ACCT, "2205 KIRBY DR"),
        MULTI_BLD_ACCT: _make_property(MULTI_BLD_ACCT, "815 HEIGHTS BLVD"),
    }
    db.add_all(props.values())
    db.flush()
    return props


def _by_acct(db: Session, acct: str) -> Property:
    return db.execute(
        select(Property).where(Property.hcad_account_id == acct)
    ).scalar_one()


# ---------------------------------------------------------------------------
# member-kind detection
# ---------------------------------------------------------------------------


def test_detect_member_kind_building_res() -> None:
    assert detect_member_kind(BUILDING_RES_HEADER.split("\t")) == KIND_BUILDING_RES


def test_detect_member_kind_fixtures() -> None:
    assert detect_member_kind(FIXTURES_HEADER.split("\t")) == KIND_FIXTURES


def test_detect_member_kind_rejects_unknown_header() -> None:
    with pytest.raises(BuildingParseError):
        detect_member_kind(["acct", "str_num", "str", "str_sfx"])  # real_acct layout


def test_import_rejects_empty_file(tmp_path: Path, db: Session) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(BuildingParseError):
        import_building_file(db, empty)


# ---------------------------------------------------------------------------
# building_res.txt: quality_code + year_remodeled
# ---------------------------------------------------------------------------


def test_building_res_import_populates_quality_and_remodel(
    db: Session, bootstrapped: dict[str, Property]
) -> None:
    stats = import_building_file(db, BUILDING_RES_FIXTURE)

    assert stats.kind == KIND_BUILDING_RES
    assert stats.rows_read == 6
    assert stats.rows_rejected == 1  # the garbage row, counted never fatal
    assert stats.accounts_seen == 4
    assert stats.accounts_matched == 3
    assert stats.accounts_unmatched == 1
    assert stats.properties_updated == 2  # MISSING_ACCT stays all-NULL -> unchanged
    assert stats.properties_unchanged == 1

    remodeled = _by_acct(db, REMODELED_ACCT)
    assert remodeled.quality_code == "B"
    assert remodeled.year_remodeled == 2016

    missing = _by_acct(db, MISSING_ACCT)
    assert missing.quality_code is None
    assert missing.year_remodeled is None

    multi = _by_acct(db, MULTI_BLD_ACCT)  # smallest bld_num (primary building) wins
    assert multi.quality_code == "B"
    assert multi.year_remodeled == 2019

    # The unmatched account must never be manufactured into a property.
    assert (
        db.execute(
            select(Property).where(Property.hcad_account_id == UNMATCHED_ACCT)
        ).scalar_one_or_none()
        is None
    )


def test_building_res_import_is_idempotent(
    db: Session, bootstrapped: dict[str, Property]
) -> None:
    import_building_file(db, BUILDING_RES_FIXTURE)
    again = import_building_file(db, BUILDING_RES_FIXTURE)

    assert again.properties_updated == 0
    assert again.properties_unchanged == 3
    assert again.accounts_matched == 3
    remodeled = _by_acct(db, REMODELED_ACCT)
    assert (remodeled.quality_code, remodeled.year_remodeled) == ("B", 2016)


def test_building_res_rejects_garbage_year(tmp_path: Path, db: Session) -> None:
    """A non-integer yr_remodel rejects that row only (counted, never fatal)."""
    prop = _make_property(REMODELED_ACCT, "1234 WESTHEIMER RD")
    db.add(prop)
    db.flush()

    header_fields = BUILDING_RES_HEADER.split("\t")
    row = {name: "" for name in header_fields}
    row.update(acct=REMODELED_ACCT, bld_num="1", qa_cd="B ", yr_remodel="20X6")
    target = tmp_path / "building_res.txt"
    target.write_text(
        BUILDING_RES_HEADER
        + "\r\n"
        + "\t".join(row[name] for name in header_fields)
        + "\r\n",
        encoding="utf-8",
    )

    stats = import_building_file(db, target)
    assert stats.rows_read == 1
    assert stats.rows_rejected == 1
    assert stats.accounts_seen == 0
    refreshed = _by_acct(db, REMODELED_ACCT)
    assert refreshed.quality_code is None and refreshed.year_remodeled is None


# ---------------------------------------------------------------------------
# fixtures.txt: bedrooms + bathrooms (RMB / RMF / RMH)
# ---------------------------------------------------------------------------


def test_fixtures_import_populates_room_counts(
    tmp_path: Path, db: Session, bootstrapped: dict[str, Property]
) -> None:
    stats = import_building_file(db, _write_fixtures_member(tmp_path))

    assert stats.kind == KIND_FIXTURES
    assert stats.rows_read == 9
    assert stats.rows_rejected == 2  # garbage row + NaN units
    assert stats.accounts_seen == 3  # MISSING_ACCT's only room row was rejected
    assert stats.accounts_matched == 2
    assert stats.accounts_unmatched == 1
    assert stats.properties_updated == 2

    rooms = _by_acct(db, REMODELED_ACCT)  # the 3/2/1 property
    assert (rooms.bedrooms, rooms.bathrooms_full, rooms.bathrooms_half) == (3, 2, 1)

    multi = _by_acct(db, MULTI_BLD_ACCT)  # bedrooms summed across buildings
    assert multi.bedrooms == 3
    assert multi.bathrooms_full is None  # no RMF/RMH rows -> never a manufactured 0
    assert multi.bathrooms_half is None

    missing = _by_acct(db, MISSING_ACCT)
    assert (missing.bedrooms, missing.bathrooms_full, missing.bathrooms_half) == (
        None,
        None,
        None,
    )


def test_fixtures_import_is_idempotent(
    tmp_path: Path, db: Session, bootstrapped: dict[str, Property]
) -> None:
    member = _write_fixtures_member(tmp_path)
    import_building_file(db, member)
    again = import_building_file(db, member)

    assert again.properties_updated == 0
    assert again.properties_unchanged == 2
    rooms = _by_acct(db, REMODELED_ACCT)
    assert (rooms.bedrooms, rooms.bathrooms_full, rooms.bathrooms_half) == (3, 2, 1)


def test_both_members_compose_without_clobbering(
    tmp_path: Path, db: Session, bootstrapped: dict[str, Property]
) -> None:
    import_building_file(db, BUILDING_RES_FIXTURE)
    import_building_file(db, _write_fixtures_member(tmp_path))

    prop = _by_acct(db, REMODELED_ACCT)
    assert prop.quality_code == "B"
    assert prop.year_remodeled == 2016
    assert (prop.bedrooms, prop.bathrooms_full, prop.bathrooms_half) == (3, 2, 1)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def test_cli_imports_both_member_files(
    tmp_path: Path,
    db: Session,
    bootstrapped: dict[str, Property],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind = db.get_bind()

    def _factory() -> Session:
        return Session(
            bind=bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )

    monkeypatch.setattr("app.core.db.SessionLocal", _factory)

    exit_code = building.main(
        [
            "--file",
            str(BUILDING_RES_FIXTURE),
            "--file",
            str(_write_fixtures_member(tmp_path)),
        ]
    )
    assert exit_code == 0

    db.expire_all()
    prop = _by_acct(db, REMODELED_ACCT)
    assert prop.quality_code == "B"
    assert prop.year_remodeled == 2016
    assert (prop.bedrooms, prop.bathrooms_full, prop.bathrooms_half) == (3, 2, 1)


def test_cli_rejects_unrecognized_member(
    tmp_path: Path, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    bind = db.get_bind()

    def _factory() -> Session:
        return Session(
            bind=bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )

    monkeypatch.setattr("app.core.db.SessionLocal", _factory)

    bogus = tmp_path / "real_acct.txt"
    bogus.write_text("acct\tstr_num\tstr\n123\t1\tMAIN\n", encoding="utf-8")
    assert building.main(["--file", str(bogus)]) == 2
