"""Record-to-property matching ladder (spec section 30).

Deterministic descent, strongest identity first:

1. ``HCAD_ID``        — hcad_account_id exact          -> 1.0,  AUTO_ACCEPTED
2. ``EXACT_ADDRESS``  — properties.normalized_address  -> 0.99, AUTO_ACCEPTED
3. ``ADDRESS_ALIAS``  — property_addresses alias exact -> 0.97, AUTO_ACCEPTED
4. ``FUZZY_ADDRESS``  — pg_trgm similarity >= 0.55     -> sim,  REVIEW_REQUIRED
5. unmatched          — property_id None               -> 0.0,  UNMATCHED

A fuzzy hit records the candidate property but is never auto-attached: the
runner only creates ledger events for AUTO_ACCEPTED matches. Every result
carries a human-readable ``reason``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import MatchMethod, MatchReviewStatus
from app.ingestion.base import NormalizedRecord
from app.models import Property, PropertyAddress

FUZZY_SIMILARITY_THRESHOLD: Final[float] = 0.55


@dataclass
class MatchResult:
    """Outcome of one descent through the matching ladder."""

    property_id: uuid.UUID | None
    method: str | None
    confidence: float
    reason: str
    review_status: str


def match_record_to_property(session: Session, rec: NormalizedRecord) -> MatchResult:
    """Match one normalized source record to a canonical property."""
    hcad_account_id = (rec.hcad_account_id or "").strip()
    if hcad_account_id:
        property_id = session.execute(
            select(Property.id)
            .where(Property.hcad_account_id == hcad_account_id)
            .order_by(Property.id)
            .limit(1)
        ).scalar_one_or_none()
        if property_id is not None:
            return MatchResult(
                property_id=property_id,
                method=MatchMethod.HCAD_ID,
                confidence=1.0,
                reason=f"hcad_account_id {hcad_account_id} exact",
                review_status=MatchReviewStatus.AUTO_ACCEPTED,
            )

    normalized_address = (rec.normalized_address or "").strip()
    if normalized_address:
        property_id = session.execute(
            select(Property.id)
            .where(Property.normalized_address == normalized_address)
            .order_by(Property.id)
            .limit(1)
        ).scalar_one_or_none()
        if property_id is not None:
            return MatchResult(
                property_id=property_id,
                method=MatchMethod.EXACT_ADDRESS,
                confidence=0.99,
                reason=f"normalized address '{normalized_address}' exact"
                " on properties.normalized_address",
                review_status=MatchReviewStatus.AUTO_ACCEPTED,
            )

        property_id = session.execute(
            select(PropertyAddress.property_id)
            .where(PropertyAddress.normalized_address == normalized_address)
            .order_by(PropertyAddress.id)
            .limit(1)
        ).scalar_one_or_none()
        if property_id is not None:
            return MatchResult(
                property_id=property_id,
                method=MatchMethod.ADDRESS_ALIAS,
                confidence=0.97,
                reason=f"normalized address '{normalized_address}' exact"
                " on property_addresses alias",
                review_status=MatchReviewStatus.AUTO_ACCEPTED,
            )

        similarity = func.similarity(Property.normalized_address, normalized_address)
        fuzzy_row = session.execute(
            select(Property.id, Property.normalized_address, similarity.label("sim"))
            .where(similarity >= FUZZY_SIMILARITY_THRESHOLD)
            .order_by(similarity.desc(), Property.id)
            .limit(1)
        ).first()
        if fuzzy_row is not None:
            sim = float(fuzzy_row.sim)
            return MatchResult(
                property_id=fuzzy_row.id,
                method=MatchMethod.FUZZY_ADDRESS,
                confidence=sim,
                reason=(
                    f"similarity {sim:.2f} >= {FUZZY_SIMILARITY_THRESHOLD}"
                    f" vs '{fuzzy_row.normalized_address}'"
                ),
                review_status=MatchReviewStatus.REVIEW_REQUIRED,
            )

    identifiers: list[str] = []
    if hcad_account_id:
        identifiers.append(f"hcad_account_id {hcad_account_id}")
    if normalized_address:
        identifiers.append(
            f"address '{normalized_address}'"
            f" (best similarity < {FUZZY_SIMILARITY_THRESHOLD})"
        )
    reason = (
        "no property matched " + " or ".join(identifiers)
        if identifiers
        else "record carries no hcad_account_id or address to match"
    )
    return MatchResult(
        property_id=None,
        method=None,
        confidence=0.0,
        reason=reason,
        review_status=MatchReviewStatus.UNMATCHED,
    )
