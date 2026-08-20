"""Houston building-permits ingestion (City of Houston weekly permit reports).

The city publishes no per-address permit API; the compliant source is the
weekly "Web eReport" permit activity XLSX officially published on
https://www.houstonpermittingcenter.org/sold-permits-search.

Package layout:

- ``parse.py``     — report-file reader (.xlsx via stdlib, or a .csv export)
- ``normalize.py`` — pure row -> :class:`~app.ingestion.base.NormalizedRecord` mapping
- ``adapter.py``   — :class:`HoustonPermitsAdapter` (fetch/parse/normalize)
- ``sync.py``      — CLI ``python -m app.ingestion.houston_permits.sync [--file PATH | --url URL]``
"""

from app.ingestion.houston_permits.adapter import HoustonPermitsAdapter

__all__ = ["HoustonPermitsAdapter"]
