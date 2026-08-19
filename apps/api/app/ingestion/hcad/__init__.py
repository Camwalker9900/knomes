"""HCAD (Harris Central Appraisal District) real_acct adapter.

HCAD is the canonical property bootstrap: it creates and updates properties
rather than only attaching ledger events. See ``app.ingestion.hcad.sync`` for
the COPY-based import pipeline.
"""

from app.ingestion.hcad.normalize import HCADAdapter

__all__ = ["HCADAdapter"]
