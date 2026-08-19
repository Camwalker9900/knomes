"""Resolution matching engine (spec section 32): the computer proposes, a human approves.

Given a finding, look for later permit events on the same property whose
title/summary contains a permit-category keyword mapped from the finding's
category. Matches are persisted as ``ResolutionCandidate`` rows in status
``REVIEW_REQUIRED`` and audited. This module NEVER mutates ``finding.status``
— moving a finding through its lifecycle is a human decision (spec section 13).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import CandidateStatus, EventType
from app.models import AuditLog, Finding, LedgerEvent, ResolutionCandidate

logger = logging.getLogger(__name__)

# Rule table (spec section 32 / plan Task E4.2): finding category -> permit-type
# keywords that must appear as whole uppercased words in the event title/summary.
CATEGORY_PERMIT_KEYWORDS: Final[dict[str, frozenset[str]]] = {
    "HVAC": frozenset({"MECHANICAL", "HVAC"}),
    "ELECTRICAL": frozenset({"ELECTRICAL"}),
    "PLUMBING": frozenset({"PLUMBING"}),
    "SEWER": frozenset({"PLUMBING"}),
    "ROOF": frozenset({"BUILDING"}),
    "FOUNDATION": frozenset({"BUILDING"}),
    "STRUCTURAL": frozenset({"BUILDING"}),
}

_PERMIT_EVENT_TYPES: Final[tuple[str, str]] = (
    EventType.PERMIT_ISSUED.value,
    EventType.PERMIT_FINALIZED.value,
)

_BASE_SCORES: Final[dict[str, float]] = {
    EventType.PERMIT_FINALIZED.value: 0.90,
    EventType.PERMIT_ISSUED.value: 0.75,
}

_RECENCY_WINDOW: Final[timedelta] = timedelta(days=180)
_RECENCY_BONUS: Final[float] = 0.05
_SCORE_CAP: Final[float] = 0.95

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z0-9]+")


def _tokenize(*texts: str | None) -> set[str]:
    """Uppercased word tokens across the given texts (None-safe)."""
    tokens: set[str] = set()
    for text in texts:
        if text:
            tokens.update(_WORD_RE.findall(text.upper()))
    return tokens


def _score(event_type: str, event_date: datetime, first_observed_at: datetime) -> float:
    """0.9 PERMIT_FINALIZED / 0.75 PERMIT_ISSUED, +0.05 within 180 days, capped at 0.95."""
    score = _BASE_SCORES[event_type]
    if event_date - first_observed_at <= _RECENCY_WINDOW:
        score += _RECENCY_BONUS
    return min(score, _SCORE_CAP)


def find_candidate_resolutions(session: Session, finding: Finding) -> list[ResolutionCandidate]:
    """Propose permit events that may resolve ``finding``; return the newly created candidates.

    Candidate events are non-retracted PERMIT_ISSUED / PERMIT_FINALIZED events on
    the same property dated strictly after ``finding.first_observed_at`` whose
    title/summary contains a permit keyword mapped from the finding's category.
    Each new match is persisted as a ``ResolutionCandidate`` (status
    REVIEW_REQUIRED) plus an ``AuditLog`` row; a (finding_id, candidate_event_id)
    pair that already exists is skipped, so repeated invocation is idempotent.

    This function only flushes — committing is the caller's responsibility — and
    never touches ``finding.status`` (spec section 13: human decision).
    """
    keywords = CATEGORY_PERMIT_KEYWORDS.get(finding.category)
    first_observed_at = finding.first_observed_at
    if keywords is None or first_observed_at is None:
        return []

    existing_event_ids: set[uuid.UUID] = set(
        session.scalars(
            select(ResolutionCandidate.candidate_event_id).where(
                ResolutionCandidate.finding_id == finding.id
            )
        )
    )

    events = session.scalars(
        select(LedgerEvent)
        .where(
            LedgerEvent.property_id == finding.property_id,
            LedgerEvent.event_type.in_(_PERMIT_EVENT_TYPES),
            LedgerEvent.event_date > first_observed_at,
            LedgerEvent.retracted_at.is_(None),
        )
        .order_by(LedgerEvent.event_date, LedgerEvent.id)
    ).all()

    created: list[ResolutionCandidate] = []
    for event in events:
        if event.id in existing_event_ids:
            continue
        # Match against the title only: permit titles are structured
        # ("Mechanical permit issued"), while summaries are free text where an
        # incidental word ("...ducts near the roof line") would propose a
        # permit against an unrelated finding category.
        matched = keywords & _tokenize(event.title)
        if not matched:
            continue

        score = _score(event.event_type, event.event_date, first_observed_at)
        rule = (
            f"{finding.category}->{{{', '.join(sorted(keywords))}}}: "
            f"{event.event_type} on same property after first_observed_at "
            f"matched keyword(s) {', '.join(sorted(matched))}"
        )
        candidate = ResolutionCandidate(
            finding_id=finding.id,
            candidate_event_id=event.id,
            match_score=score,
            status=CandidateStatus.REVIEW_REQUIRED.value,
            rule=rule,
        )
        session.add(candidate)
        session.flush()

        session.add(
            AuditLog(
                actor_type="system",
                action="resolution_candidate_proposed",
                entity_type="resolution_candidates",
                entity_id=candidate.id,
                new_value={
                    "finding_id": str(finding.id),
                    "candidate_event_id": str(event.id),
                    "match_score": score,
                    "status": CandidateStatus.REVIEW_REQUIRED.value,
                    "rule": rule,
                },
            )
        )
        created.append(candidate)
        logger.info(
            "resolution candidate proposed",
            extra={
                "finding_id": str(finding.id),
                "candidate_event_id": str(event.id),
                "match_score": score,
                "rule": rule,
            },
        )

    if created:
        session.flush()
    return created
