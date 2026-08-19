"""Matching ladder tests: HCAD id -> exact address -> alias -> fuzzy -> unmatched.

Fuzzy similarity values were verified against pg_trgm:
similarity('100 TEST ST', '100 TESTT ST') = 0.846 (>= 0.55)
similarity('100 TEST ST', '200 BEST ST')  = 0.294 (<  0.55)
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.enums import MatchMethod, MatchReviewStatus
from app.ingestion.base import NormalizedRecord
from app.lib.address import address_hash, normalize_address
from app.models import Property, PropertyAddress
from app.services.matching import FUZZY_SIMILARITY_THRESHOLD, match_record_to_property


def _make_property(
    db: Session, *, address_line1: str, hcad_account_id: str | None = None
) -> Property:
    normalized = normalize_address(address_line1)
    prop = Property(
        hcad_account_id=hcad_account_id,
        address_line1=address_line1,
        city="Houston",
        state="TX",
        postal_code="77000",
        normalized_address=normalized,
        address_hash=address_hash(normalized),
    )
    db.add(prop)
    db.flush()
    return prop


def _record(*, hcad: str | None = None, address: str | None = None) -> NormalizedRecord:
    return NormalizedRecord(
        record_type="test_record",
        source_record_id="rec-1",
        raw_payload={"id": "rec-1"},
        normalized_address=address,
        hcad_account_id=hcad,
        event_candidates=[],
        raw_address=address,
    )


def test_hcad_id_rung_auto_accepts_with_full_confidence(db: Session) -> None:
    prop = _make_property(db, address_line1="100 Test Street", hcad_account_id="TEST000100")

    result = match_record_to_property(db, _record(hcad="TEST000100"))

    assert result.property_id == prop.id
    assert result.method == MatchMethod.HCAD_ID
    assert result.confidence == 1.0
    assert result.review_status == MatchReviewStatus.AUTO_ACCEPTED
    assert "TEST000100" in result.reason


def test_hcad_id_takes_precedence_over_exact_address(db: Session) -> None:
    prop_by_id = _make_property(db, address_line1="100 Test Street", hcad_account_id="TEST000100")
    prop_by_addr = _make_property(db, address_line1="200 Best Street")

    result = match_record_to_property(
        db, _record(hcad="TEST000100", address=prop_by_addr.normalized_address)
    )

    assert result.property_id == prop_by_id.id
    assert result.method == MatchMethod.HCAD_ID


def test_exact_address_rung(db: Session) -> None:
    prop = _make_property(db, address_line1="100 Test Street")

    result = match_record_to_property(db, _record(address="100 TEST ST"))

    assert result.property_id == prop.id
    assert result.method == MatchMethod.EXACT_ADDRESS
    assert result.confidence == 0.99
    assert result.review_status == MatchReviewStatus.AUTO_ACCEPTED
    assert "100 TEST ST" in result.reason


def test_unknown_hcad_id_falls_through_to_address(db: Session) -> None:
    prop = _make_property(db, address_line1="100 Test Street")

    result = match_record_to_property(db, _record(hcad="NOPE999999", address="100 TEST ST"))

    assert result.property_id == prop.id
    assert result.method == MatchMethod.EXACT_ADDRESS
    assert result.review_status == MatchReviewStatus.AUTO_ACCEPTED


def test_address_alias_rung(db: Session) -> None:
    prop = _make_property(db, address_line1="100 Test Street")
    alias_normalized = normalize_address("100 Olde Towne Road")
    db.add(
        PropertyAddress(
            property_id=prop.id,
            raw_address="100 Olde Towne Road",
            normalized_address=alias_normalized,
            source="test_fixture",
        )
    )
    db.flush()

    result = match_record_to_property(db, _record(address=alias_normalized))

    assert result.property_id == prop.id
    assert result.method == MatchMethod.ADDRESS_ALIAS
    assert result.confidence == 0.97
    assert result.review_status == MatchReviewStatus.AUTO_ACCEPTED
    assert alias_normalized in result.reason


def test_fuzzy_rung_attaches_candidate_but_requires_review(db: Session) -> None:
    prop = _make_property(db, address_line1="100 Test Street")

    result = match_record_to_property(db, _record(address="100 TESTT ST"))

    assert result.property_id == prop.id
    assert result.method == MatchMethod.FUZZY_ADDRESS
    assert FUZZY_SIMILARITY_THRESHOLD <= result.confidence < 0.99
    assert result.review_status == MatchReviewStatus.REVIEW_REQUIRED
    assert f">= {FUZZY_SIMILARITY_THRESHOLD}" in result.reason
    assert "100 TEST ST" in result.reason


def test_below_threshold_is_unmatched_and_never_attaches(db: Session) -> None:
    _make_property(db, address_line1="100 Test Street")

    result = match_record_to_property(db, _record(address="200 BEST ST"))

    assert result.property_id is None
    assert result.method is None
    assert result.confidence == 0.0
    assert result.review_status == MatchReviewStatus.UNMATCHED
    assert "no property matched" in result.reason
    assert "200 BEST ST" in result.reason


def test_record_without_identifiers_is_unmatched(db: Session) -> None:
    _make_property(db, address_line1="100 Test Street")

    result = match_record_to_property(db, _record())

    assert result.property_id is None
    assert result.method is None
    assert result.review_status == MatchReviewStatus.UNMATCHED
    assert result.reason == "record carries no hcad_account_id or address to match"
