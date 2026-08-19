"""Houston Building Code Enforcement ingestion (City of Houston CKAN open data).

Package layout:

- ``adapter.py``   — :class:`HoustonCodeAdapter` (fetch/parse/normalize)
- ``normalize.py`` — pure record -> :class:`~app.ingestion.base.NormalizedRecord` mapping
- ``sync.py``      — CLI entrypoint ``python -m app.ingestion.houston_code.sync [--file PATH]``
"""

from app.ingestion.houston_code.adapter import HoustonCodeAdapter

__all__ = ["HoustonCodeAdapter"]
