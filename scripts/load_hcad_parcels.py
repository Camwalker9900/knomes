#!/usr/bin/env python3
"""Load HCAD GIS parcel geometries into ``properties.parcel_geometry``.

RUN MANUALLY — never from tests or CI. Input is a *local* copy of the HCAD
GIS parcels shapefile (zip, directory, or bare ``.shp``). Download it yourself
first from HCAD's public GIS downloads (the only allowed host):

    https://download.hcad.org/data/GIS/Parcels.zip   (~196 MB zip)

Pipeline (idempotent — rerunning with the same file changes zero rows):

1. ``ogrinfo`` (via the official GDAL docker image) inspects the layer to find
   the HCAD account attribute (``HCAD_NUM``, ``LowParcelI``, ... — or pass
   ``--account-attr``).
2. ``ogr2ogr`` (same docker image) bulk-loads account + geometry into a
   staging table (dropped/recreated every run, ``-overwrite``), promoting to
   MULTIPOLYGON and reprojecting to EPSG:4326.
3. A single set-based UPDATE copies each account's geometry into
   ``properties.parcel_geometry`` and fills ``latitude``/``longitude`` from the
   centroid — only where the stored geometry ``IS DISTINCT FROM`` the staged
   one, so re-imports are no-ops. Invalid rings are repaired in the database
   via ``ST_MakeValid`` (the alpine-small GDAL image has no GEOS).
4. The staging table is dropped (keep it with ``--keep-staging``).

No Python dependencies beyond the standard library are needed to *stage*
(docker does the GDAL work); the SQL step needs ``psycopg``, which is already
in the API environment:

    cd apps/api && uv run python ../../scripts/load_hcad_parcels.py \\
        ~/Downloads/Parcels.zip

LICENSING NOTE: Harris Central Appraisal District public data. Review HCAD's
terms before redistributing; never commit real parcel extracts to the repo.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "ghcr.io/osgeo/gdal:alpine-small-latest"
DEFAULT_DOCKER_HOST = "host.docker.internal"
DEFAULT_STAGING_TABLE = "hcad_parcels_staging"
GEOMETRY_COLUMN = "geom"
PARCELS_SOURCE_URL = "https://download.hcad.org/data/GIS/Parcels.zip"

# Account-number attribute names seen on HCAD GIS layers, in preference order.
ACCOUNT_ATTR_CANDIDATES = (
    "HCAD_NUM",
    "ACCOUNT",
    "ACCT_NUM",
    "ACCTNUM",
    "ACCT",
    "LOWPARCELI",  # DBF-truncated "LowParcelId"
    "LOWPARCELID",
    "PARCEL_ID",
    "PARCELID",
)

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_FIELD_LINE_RE = re.compile(
    r"^\s*([A-Za-z0-9_]+):\s*"
    r"(String|Integer64|Integer|Real|Date|Time|DateTime|Binary|StringList)\b"
)

log = logging.getLogger("load_hcad_parcels")


class SupportsCursor(Protocol):
    """Minimal DB-API cursor surface the SQL step needs (psycopg-compatible)."""

    rowcount: int

    def execute(self, query: str) -> object: ...

    def fetchone(self) -> tuple[object, ...] | None: ...


def _ident(name: str) -> str:
    """Validate an SQL identifier (staging table / column names) — no quoting games."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def build_update_sql(
    staging_table: str = DEFAULT_STAGING_TABLE,
    account_column: str = "hcad_num",
    geometry_column: str = GEOMETRY_COLUMN,
) -> str:
    """The set-based UPDATE that copies staged geometries into ``properties``.

    Joins on the *trimmed* account number, repairs invalid rings with
    ``ST_MakeValid`` (keeping only polygonal parts), deduplicates staging rows
    per account (largest polygon wins, e.g. stacked condo parcels), and
    touches a row only when the geometry actually differs (or lat/lon are
    missing) so the statement is idempotent.
    """
    staging = _ident(staging_table)
    acct = _ident(account_column)
    geom = _ident(geometry_column)
    return f"""
        UPDATE properties AS p
        SET parcel_geometry = s.geom,
            latitude = ST_Y(ST_Centroid(s.geom)),
            longitude = ST_X(ST_Centroid(s.geom)),
            updated_at = now()
        FROM (
            SELECT DISTINCT ON (acct) acct, geom
            FROM (
                SELECT btrim({acct}::text) AS acct,
                       ST_Multi(ST_CollectionExtract(ST_MakeValid({geom}), 3)) AS geom,
                       ST_Area({geom}) AS area
                FROM {staging}
                WHERE {acct} IS NOT NULL
                  AND btrim({acct}::text) <> ''
                  AND {geom} IS NOT NULL
            ) AS cleaned
            WHERE NOT ST_IsEmpty(geom)
            ORDER BY acct, area DESC
        ) AS s
        WHERE p.hcad_account_id = s.acct
          AND (p.parcel_geometry IS DISTINCT FROM s.geom
               OR p.latitude IS NULL
               OR p.longitude IS NULL)
    """


def apply_parcel_geometry(
    cur: SupportsCursor,
    staging_table: str = DEFAULT_STAGING_TABLE,
    account_column: str = "hcad_num",
    geometry_column: str = GEOMETRY_COLUMN,
) -> dict[str, int]:
    """Run the SQL step against an open DB-API cursor; returns row counts.

    Importable without docker/ogr2ogr so tests can drive it against a
    hand-built staging table.
    """
    staging = _ident(staging_table)
    stats: dict[str, int] = {}

    cur.execute(f"SELECT count(*) FROM {staging}")
    row = cur.fetchone()
    stats["staging_rows"] = int(str(row[0])) if row and row[0] is not None else 0

    cur.execute(build_update_sql(staging_table, account_column, geometry_column))
    stats["properties_updated"] = cur.rowcount

    cur.execute("SELECT count(*) FROM properties WHERE parcel_geometry IS NOT NULL")
    row = cur.fetchone()
    stats["properties_with_geometry"] = int(str(row[0])) if row and row[0] is not None else 0
    return stats


def pick_account_field(fields: list[str], override: str | None = None) -> str:
    """Choose the account attribute from the layer's field names."""
    if override:
        for field in fields:
            if field.upper() == override.upper():
                return field
        raise SystemExit(
            f"error: --account-attr {override!r} not found in layer fields: {fields}"
        )
    by_upper = {field.upper(): field for field in fields}
    for candidate in ACCOUNT_ATTR_CANDIDATES:
        if candidate in by_upper:
            return by_upper[candidate]
    raise SystemExit(
        "error: could not autodetect the HCAD account attribute in layer fields "
        f"{fields}; pass --account-attr explicitly"
    )


def resolve_database_url(cli_url: str | None) -> str:
    """CLI arg -> DATABASE_URL env -> apps/api settings.database_url."""
    if cli_url:
        return cli_url
    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        return env_url
    try:
        sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
        from app.core.config import settings

        return settings.database_url
    except Exception:
        raise SystemExit(
            "error: no database URL. Pass --database-url or set DATABASE_URL."
        ) from None


def pg_dsn_parts(database_url: str, docker_host: str | None) -> dict[str, str]:
    """Split a postgresql[+driver]:// URL into libpq parts.

    When ``docker_host`` is given, localhost is rewritten to it so the GDAL
    container can reach the host's published port (e.g. host.docker.internal).
    """
    url = urlparse(re.sub(r"^postgresql\+[a-z0-9]+:", "postgresql:", database_url))
    if url.scheme != "postgresql":
        raise SystemExit(f"error: unsupported database URL scheme in {database_url!r}")
    host = url.hostname or "localhost"
    if docker_host and host in ("localhost", "127.0.0.1", "::1"):
        host = docker_host
    return {
        "host": host,
        "port": str(url.port or 5432),
        "dbname": unquote(url.path.lstrip("/")) or "postgres",
        "user": unquote(url.username or ""),
        "password": unquote(url.password or ""),
    }


def locate_shapefile(data: Path) -> tuple[Path, str]:
    """Resolve the input into (host dir to mount, GDAL path inside /data).

    Accepts a parcels zip (read via /vsizip/, no extraction), a directory
    containing a .shp, or a bare .shp path.
    """
    data = data.expanduser().resolve()
    if not data.exists():
        raise SystemExit(
            f"error: {data} does not exist. Download the parcels zip first from "
            f"{PARCELS_SOURCE_URL} (manual step; see script docstring)."
        )
    if data.is_file() and data.suffix.lower() == ".zip":
        with zipfile.ZipFile(data) as archive:
            members = [n for n in archive.namelist() if n.lower().endswith(".shp")]
        if not members:
            raise SystemExit(f"error: no .shp member inside {data.name}")
        if len(members) > 1:
            log.warning("multiple .shp members in %s; using %s", data.name, members[0])
        return data.parent, f"/vsizip//data/{data.name}/{members[0]}"
    if data.is_dir():
        shps = sorted(data.glob("*.shp")) or sorted(data.rglob("*.shp"))
        if not shps:
            raise SystemExit(f"error: no .shp file found under {data}")
        if len(shps) > 1:
            log.warning("multiple .shp files under %s; using %s", data, shps[0].name)
        return shps[0].parent, f"/data/{shps[0].name}"
    if data.suffix.lower() == ".shp":
        return data.parent, f"/data/{data.name}"
    raise SystemExit(f"error: expected a .zip, directory, or .shp path, got {data}")


def _docker_base(image: str, mount_dir: Path) -> list[str]:
    return ["docker", "run", "--rm", "-v", f"{mount_dir}:/data:ro", image]


def inspect_layer(image: str, mount_dir: Path, gdal_path: str) -> tuple[str, list[str]]:
    """Run ogrinfo in docker; return (layer name, attribute field names)."""
    cmd = [*_docker_base(image, mount_dir), "ogrinfo", "-ro", "-so", "-al", gdal_path]
    log.info("inspecting layer: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"error: ogrinfo failed with exit code {result.returncode}")
    layer_name = ""
    fields: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("Layer name:"):
            layer_name = line.split(":", 1)[1].strip()
        match = _FIELD_LINE_RE.match(line)
        if match:
            fields.append(match.group(1))
    if not layer_name:
        raise SystemExit("error: could not read layer name from ogrinfo output")
    log.info("layer %r fields: %s", layer_name, ", ".join(fields))
    return layer_name, fields


def stage_with_ogr2ogr(
    image: str,
    mount_dir: Path,
    gdal_path: str,
    layer_name: str,
    account_attr: str,
    dsn: dict[str, str],
    staging_table: str,
) -> None:
    """Bulk-load account + geometry into the staging table (drop/recreate)."""
    pg = (
        f"PG:host={dsn['host']} port={dsn['port']} dbname={dsn['dbname']} "
        f"user={dsn['user']} password={dsn['password']}"
    )
    cmd = [
        *_docker_base(image, mount_dir),
        "ogr2ogr",
        "-f",
        "PostgreSQL",
        pg,
        gdal_path,
        layer_name,
        "-nln",
        staging_table,
        "-overwrite",
        "-nlt",
        "PROMOTE_TO_MULTI",
        "-t_srs",
        "EPSG:4326",
        "-dim",
        "XY",
        "-select",
        account_attr,
        "-lco",
        f"GEOMETRY_NAME={GEOMETRY_COLUMN}",
        "-lco",
        "SPATIAL_INDEX=NONE",
        "-progress",
        "--config",
        "PG_USE_COPY",
        "YES",
    ]
    shown = " ".join(part if not part.startswith("PG:") else "PG:<redacted>" for part in cmd)
    log.info("staging parcels: %s", shown)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit(f"error: ogr2ogr failed with exit code {result.returncode}")
    log.info("staging load complete: table %s", staging_table)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Load HCAD GIS parcel geometries (local Parcels.zip / shapefile) into "
            "properties.parcel_geometry, matched on the HCAD account number. "
            "Uses ogr2ogr from the official GDAL docker image; idempotent."
        ),
        epilog=f"Source download (manual): {PARCELS_SOURCE_URL}",
    )
    parser.add_argument(
        "data", type=Path, help="path to Parcels.zip, a directory with the .shp, or the .shp"
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="postgresql+psycopg:// URL (default: DATABASE_URL env, then API settings)",
    )
    parser.add_argument(
        "--account-attr",
        default=None,
        help="HCAD account attribute on the layer (default: autodetect, e.g. HCAD_NUM)",
    )
    parser.add_argument(
        "--image", default=DEFAULT_IMAGE, help=f"GDAL docker image (default {DEFAULT_IMAGE})"
    )
    parser.add_argument(
        "--docker-host",
        default=DEFAULT_DOCKER_HOST,
        help=(
            "hostname the GDAL container uses to reach a localhost Postgres "
            f"(default {DEFAULT_DOCKER_HOST}; pass '' to keep the URL host)"
        ),
    )
    parser.add_argument(
        "--staging-table",
        default=DEFAULT_STAGING_TABLE,
        help=f"staging table name (default {DEFAULT_STAGING_TABLE})",
    )
    parser.add_argument(
        "--keep-staging",
        action="store_true",
        help="keep the staging table after the update (default: drop it)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    staging_table = _ident(args.staging_table)
    database_url = resolve_database_url(args.database_url)
    docker_dsn = pg_dsn_parts(database_url, args.docker_host or None)
    mount_dir, gdal_path = locate_shapefile(args.data)

    layer_name, fields = inspect_layer(args.image, mount_dir, gdal_path)
    account_attr = pick_account_field(fields, args.account_attr)
    log.info("using account attribute %r on layer %r", account_attr, layer_name)

    stage_with_ogr2ogr(
        args.image, mount_dir, gdal_path, layer_name, account_attr, docker_dsn, staging_table
    )

    try:
        import psycopg
    except ImportError:
        raise SystemExit(
            "error: psycopg is required for the SQL step. Run via the API env:\n"
            "  cd apps/api && uv run python ../../scripts/load_hcad_parcels.py ..."
        ) from None

    # OGR launders shapefile attribute names to lowercase in PostgreSQL.
    account_column = account_attr.lower()
    local_dsn = pg_dsn_parts(database_url, docker_host=None)
    conninfo = " ".join(f"{key}={value}" for key, value in local_dsn.items() if value)
    with psycopg.connect(conninfo) as conn, conn.cursor() as cur:
        log.info("applying parcel geometries to properties ...")
        stats = apply_parcel_geometry(cur, staging_table, account_column)
        if not args.keep_staging:
            cur.execute(f"DROP TABLE IF EXISTS {staging_table}")
            log.info("dropped staging table %s", staging_table)
        conn.commit()

    log.info(
        "done: staging_rows=%d properties_updated=%d properties_with_geometry=%d",
        stats["staging_rows"],
        stats["properties_updated"],
        stats["properties_with_geometry"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
