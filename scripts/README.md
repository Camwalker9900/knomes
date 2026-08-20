# scripts/ — manual data utilities

Run these by hand, never from tests or CI. They talk only to the allowed
hosts (`download.hcad.org`, `data.houstontx.gov`) or to local files you
downloaded yourself. HCAD downloads are Harris Central Appraisal District
public data — review HCAD's terms before redistributing, and never commit
real extracts to the repo.

## fetch_hcad_sample.py

Downloads HCAD `Real_acct_owner.zip` and truncates `real_acct.txt` into
`data/fixtures/hcad_sample/` (overwrites the committed synthetic fixture by
default — restore with git if unintended).

```bash
python3 scripts/fetch_hcad_sample.py <URL> [--rows N] [--out PATH]
```

## load_hcad_parcels.py

Loads HCAD GIS parcel geometries into `properties.parcel_geometry`
(MULTIPOLYGON, EPSG:4326) and fills `latitude`/`longitude` from the parcel
centroid, matched on the HCAD account number. Idempotent: staging is
dropped/recreated each run and properties update only when the geometry
actually changed.

1. Download the parcels shapefile zip manually (~196 MB):
   `https://download.hcad.org/data/GIS/Parcels.zip`
2. Run the loader (needs docker for GDAL's `ogr2ogr` — no local GDAL install —
   and the API env for the SQL step):

```bash
cd apps/api && uv run python ../../scripts/load_hcad_parcels.py ~/Downloads/Parcels.zip
```

Useful flags: `--database-url` (default: `DATABASE_URL` env, then API
settings), `--account-attr` (default: autodetect `HCAD_NUM` /
`LowParcelI` / ...; the layer name is always autodetected), `--keep-staging`,
`--staging-table`, `--image`, `--docker-host` (default
`host.docker.internal`, how the GDAL container reaches a localhost Postgres).
