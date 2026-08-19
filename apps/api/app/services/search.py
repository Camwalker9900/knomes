"""Property search: a three-rung priority ladder per plan/CONTRACTS.md.

1. Exact normalized address (canonical column OR any alias in property_addresses)
   -> match_type EXACT_ADDRESS.
2. Exact HCAD account id (stripped, case-insensitive) -> match_type HCAD_ID.
3. pg_trgm ``similarity(normalized_address, :q) > 0.3`` ordered descending
   -> match_type FUZZY_ADDRESS.

Property ids are deduplicated across rungs (earlier rungs win) and at most
10 results are returned in total.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.enums import MatchMethod
from app.lib.address import normalize_address
from app.models import Property, PropertyAddress

MAX_RESULTS = 10
FUZZY_SIMILARITY_THRESHOLD = 0.3


def search_properties(session: Session, q: str) -> list[tuple[Property, str]]:
    """Return up to 10 ``(property, match_type)`` pairs for a raw query string."""
    results: list[tuple[Property, str]] = []
    seen: set[uuid.UUID] = set()

    def take(candidates: Iterable[Property], match_type: str) -> None:
        for candidate in candidates:
            if len(results) >= MAX_RESULTS:
                return
            if candidate.id in seen:
                continue
            seen.add(candidate.id)
            results.append((candidate, match_type))

    normalized_q = normalize_address(q)

    if normalized_q:
        alias_property_ids = select(PropertyAddress.property_id).where(
            PropertyAddress.normalized_address == normalized_q
        )
        exact_stmt = (
            select(Property)
            .where(
                or_(
                    Property.normalized_address == normalized_q,
                    Property.id.in_(alias_property_ids),
                )
            )
            .order_by(Property.normalized_address, Property.id)
            .limit(MAX_RESULTS)
        )
        take(session.scalars(exact_stmt), MatchMethod.EXACT_ADDRESS.value)

    hcad_q = q.strip().upper()
    if hcad_q and len(results) < MAX_RESULTS:
        hcad_stmt = (
            select(Property)
            .where(func.upper(Property.hcad_account_id) == hcad_q)
            .order_by(Property.id)
            .limit(MAX_RESULTS)
        )
        take(session.scalars(hcad_stmt), MatchMethod.HCAD_ID.value)

    if normalized_q and len(results) < MAX_RESULTS:
        similarity = func.similarity(Property.normalized_address, normalized_q)
        # Fetch enough rows that earlier-rung duplicates cannot starve the cap.
        fuzzy_stmt = (
            select(Property)
            .where(similarity > FUZZY_SIMILARITY_THRESHOLD)
            .order_by(similarity.desc(), Property.normalized_address, Property.id)
            .limit(MAX_RESULTS + len(seen))
        )
        take(session.scalars(fuzzy_stmt), MatchMethod.FUZZY_ADDRESS.value)

    return results
