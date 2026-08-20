"""Houston code-enforcement project actions ingestion (City of Houston CKAN).

Package layout:

- ``adapter.py``   — :class:`HoustonActionsAdapter` (fetch/parse/normalize;
  ``parse`` can gate on the linkable NPPRJID set)
- ``normalize.py`` — pure record -> :class:`~app.ingestion.base.NormalizedRecord` mapping
- ``sync.py``      — CLI entrypoint ``python -m app.ingestion.houston_actions.sync
  [--file PATH]`` plus the NPPRJID ``resolve_property`` hook
"""

from app.ingestion.houston_actions.adapter import HoustonActionsAdapter

__all__ = ["HoustonActionsAdapter"]
