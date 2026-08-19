"""Synthetic demo dataset seeding (plan Task C3).

``python -m app.seed`` seeds three synthetic Houston properties
(100/200/300 Test Street) demonstrating the core ledger arcs. Idempotent.
"""

from app.seed.synthetic import INFERRED_TERMINATION_SUMMARY, SOURCE_NAME, seed

__all__ = ["INFERRED_TERMINATION_SUMMARY", "SOURCE_NAME", "seed"]
