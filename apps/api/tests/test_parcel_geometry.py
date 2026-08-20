"""Tests for the SQL-update step of ``scripts/load_hcad_parcels.py``.

No shapefile, docker, or ogr2ogr needed: a staging table shaped exactly like
the loader's ogr2ogr output is built by hand with a synthetic MULTIPOLYGON,
then the loader's importable ``apply_parcel_geometry`` runs against it.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import Property

REPO_ROOT = Path(__file__).resolve().parents[3]
LOADER_PATH = REPO_ROOT / "scripts" / "load_hcad_parcels.py"

STAGING_TABLE = "hcad_parcels_staging_pytest"
ACCOUNT = "9990001230001"
# 0.002 x 0.002 degree square near Houston; centroid (-95.4000, 29.7000).
SQUARE_WKT = (
    "MULTIPOLYGON((("
    "-95.401 29.701,-95.399 29.701,-95.399 29.699,-95.401 29.699,-95.401 29.701"
    ")))"
)


def _load_loader_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("load_hcad_parcels", LOADER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


loader = _load_loader_module()


def _make_property(hcad_account_id: str | None, address: str) -> Property:
    return Property(
        id=uuid.uuid4(),
        hcad_account_id=hcad_account_id,
        address_line1=address,
        city="HOUSTON",
        state="TX",
        postal_code="77000",
        normalized_address=address,
        address_hash=f"testhash-{uuid.uuid4()}",
    )


@pytest.fixture()
def staging(db: Session) -> Iterator[str]:
    """Staging table shaped like the loader's ogr2ogr output (acct + geom)."""
    db.execute(
        sa.text(
            f"""
            CREATE TABLE {STAGING_TABLE} (
                ogc_fid serial PRIMARY KEY,
                hcad_num varchar,
                geom geometry(MultiPolygon, 4326)
            )
            """
        )
    )
    yield STAGING_TABLE
    # The db fixture rolls back the enclosing transaction; nothing persists.


def _cursor(db: Session) -> Any:
    """DB-API cursor on the test session's connection (same transaction)."""
    return db.connection().connection.cursor()


def _apply(db: Session) -> dict[str, int]:
    return loader.apply_parcel_geometry(
        _cursor(db), staging_table=STAGING_TABLE, account_column="hcad_num"
    )


def _insert_staging_row(db: Session, acct: str, wkt: str) -> None:
    db.execute(
        sa.text(
            f"INSERT INTO {STAGING_TABLE} (hcad_num, geom) "
            "VALUES (:acct, ST_Multi(ST_GeomFromText(:wkt, 4326)))"
        ),
        {"acct": acct, "wkt": wkt},
    )


def test_apply_sets_geometry_and_centroid(db: Session, staging: str) -> None:
    prop = _make_property(ACCOUNT, "1 GEOM TEST ST")
    untouched = _make_property("9990001230099", "2 GEOM TEST ST")
    db.add_all([prop, untouched])
    db.flush()
    # Whitespace around the staged account exercises the trimmed join.
    _insert_staging_row(db, f"  {ACCOUNT}  ", SQUARE_WKT)
    _insert_staging_row(db, "   ", SQUARE_WKT)  # blank accounts are ignored

    stats = _apply(db)

    assert stats["staging_rows"] == 2
    assert stats["properties_updated"] == 1
    row = db.execute(
        sa.text(
            "SELECT ST_AsText(parcel_geometry) AS wkt, ST_SRID(parcel_geometry) AS srid,"
            " latitude, longitude FROM properties WHERE id = :id"
        ),
        {"id": str(prop.id)},
    ).one()
    assert row.wkt is not None and row.wkt.startswith("MULTIPOLYGON")
    assert row.srid == 4326
    assert row.latitude == pytest.approx(29.700, abs=1e-6)
    assert row.longitude == pytest.approx(-95.400, abs=1e-6)

    other = db.execute(
        sa.text("SELECT parcel_geometry, latitude FROM properties WHERE id = :id"),
        {"id": str(untouched.id)},
    ).one()
    assert other.parcel_geometry is None
    assert other.latitude is None


def test_st_contains_point_query_uses_gist_index(db: Session, staging: str) -> None:
    prop = _make_property(ACCOUNT, "1 GEOM TEST ST")
    db.add(prop)
    db.flush()
    _insert_staging_row(db, ACCOUNT, SQUARE_WKT)
    _apply(db)

    hit = db.execute(
        sa.text(
            "SELECT id FROM properties WHERE ST_Contains(parcel_geometry,"
            " ST_SetSRID(ST_MakePoint(-95.4, 29.7), 4326))"
        )
    ).scalars().all()
    assert [str(value) for value in hit] == [str(prop.id)]

    # A tiny table would otherwise seq-scan; force the planner to show the index.
    db.execute(sa.text("SET LOCAL enable_seqscan = off"))
    plan_rows = db.execute(
        sa.text(
            "EXPLAIN SELECT id FROM properties WHERE ST_Contains(parcel_geometry,"
            " ST_SetSRID(ST_MakePoint(-95.4, 29.7), 4326))"
        )
    ).scalars().all()
    plan = "\n".join(plan_rows)
    assert "Index" in plan
    assert "ix_properties_parcel_geometry" in plan


def test_rerun_is_a_no_op(db: Session, staging: str) -> None:
    prop = _make_property(ACCOUNT, "1 GEOM TEST ST")
    db.add(prop)
    db.flush()
    _insert_staging_row(db, ACCOUNT, SQUARE_WKT)

    first = _apply(db)
    assert first["properties_updated"] == 1
    before = db.execute(
        sa.text(
            "SELECT parcel_geometry::text AS geom_hex, latitude, longitude, updated_at"
            " FROM properties WHERE id = :id"
        ),
        {"id": str(prop.id)},
    ).one()

    second = _apply(db)
    assert second["properties_updated"] == 0
    after = db.execute(
        sa.text(
            "SELECT parcel_geometry::text AS geom_hex, latitude, longitude, updated_at"
            " FROM properties WHERE id = :id"
        ),
        {"id": str(prop.id)},
    ).one()
    assert after == before


def test_pick_account_field_autodetect_and_override() -> None:
    assert loader.pick_account_field(["OBJECTID", "LowParcelI", "HCAD_NUM"]) == "HCAD_NUM"
    assert loader.pick_account_field(["OBJECTID", "LowParcelI"]) == "LowParcelI"
    assert loader.pick_account_field(["Foo", "hcad_num"], override="HCAD_NUM") == "hcad_num"
    with pytest.raises(SystemExit):
        loader.pick_account_field(["OBJECTID", "SHAPE_AREA"])


def test_sql_identifiers_are_validated() -> None:
    with pytest.raises(ValueError):
        loader.build_update_sql(staging_table="bad;drop table properties")
    with pytest.raises(ValueError):
        loader.build_update_sql(account_column='hcad" --')


def test_pg_dsn_parts_rewrites_localhost_for_docker() -> None:
    url = "postgresql+psycopg://knomes:knomes@localhost:5433/knomes_l1_gis"
    docker = loader.pg_dsn_parts(url, "host.docker.internal")
    assert docker["host"] == "host.docker.internal"
    assert docker["port"] == "5433"
    assert docker["dbname"] == "knomes_l1_gis"
    local = loader.pg_dsn_parts(url, None)
    assert local["host"] == "localhost"
