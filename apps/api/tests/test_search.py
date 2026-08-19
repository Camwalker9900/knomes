"""Search ladder tests: exact address (incl. aliases), HCAD id, trigram fuzzy, cap at 10."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.lib.address import address_hash, normalize_address
from app.models import Property, PropertyAddress
from app.services.search import search_properties


def make_property(db: Session, line1: str, *, hcad: str | None = None) -> Property:
    normalized = normalize_address(line1)
    prop = Property(
        address_line1=line1,
        city="Houston",
        state="TX",
        postal_code="77000",
        normalized_address=normalized,
        address_hash=address_hash(normalized),
        hcad_account_id=hcad,
    )
    db.add(prop)
    db.flush()
    return prop


class TestExactAddressRung:
    def test_exact_normalized_address_is_first_hit(self, db: Session) -> None:
        target = make_property(db, "100 Test St", hcad="TEST000100")
        make_property(db, "150 Test St")

        results = search_properties(db, "100 test street")

        assert results, "expected at least one result"
        prop, match_type = results[0]
        assert prop.id == target.id
        assert match_type == "EXACT_ADDRESS"

    def test_punctuation_and_case_variants_hit_exactly(self, db: Session) -> None:
        target = make_property(db, "100 Test St")

        for query in ("100 TEST ST.", "100 test st", "100 Test Street,", "100 tEsT sTrEeT"):
            results = search_properties(db, query)
            assert results, f"no results for {query!r}"
            prop, match_type = results[0]
            assert prop.id == target.id, f"wrong property for {query!r}"
            assert match_type == "EXACT_ADDRESS", f"wrong match type for {query!r}"

    def test_alias_table_variant_matches_as_exact(self, db: Session) -> None:
        target = make_property(db, "100 Test St")
        db.add(
            PropertyAddress(
                property_id=target.id,
                raw_address="100 Olde Test Street",
                normalized_address=normalize_address("100 Olde Test Street"),
                source="test_fixture",
            )
        )
        db.flush()

        results = search_properties(db, "100 olde test street")

        assert results[0][0].id == target.id
        assert results[0][1] == "EXACT_ADDRESS"
        # The same property must not reappear via the fuzzy rung.
        assert [prop.id for prop, _ in results].count(target.id) == 1


class TestHcadRung:
    def test_hcad_account_id_lookup(self, db: Session) -> None:
        make_property(db, "100 Test St", hcad="TEST000100")
        target = make_property(db, "200 Test St", hcad="TEST000200")

        results = search_properties(db, "TEST000200")

        assert results[0][0].id == target.id
        assert results[0][1] == "HCAD_ID"

    def test_hcad_lookup_strips_and_uppercases(self, db: Session) -> None:
        target = make_property(db, "200 Test St", hcad="TEST000200")

        results = search_properties(db, "  test000200  ")

        assert results[0][0].id == target.id
        assert results[0][1] == "HCAD_ID"


class TestFuzzyRung:
    def test_misspelling_hits_via_trigram(self, db: Session) -> None:
        target = make_property(db, "100 Test St")

        results = search_properties(db, "100 Tset Street")

        fuzzy_ids = [prop.id for prop, match_type in results if match_type == "FUZZY_ADDRESS"]
        assert target.id in fuzzy_ids

    def test_results_capped_at_ten_with_exact_first(self, db: Session) -> None:
        target = make_property(db, "100 Test St")
        for number in range(101, 113):  # 12 fuzzy-similar neighbours -> 13 candidates
            make_property(db, f"{number} Test St")

        results = search_properties(db, "100 test street")

        assert len(results) == 10
        assert results[0][0].id == target.id
        assert results[0][1] == "EXACT_ADDRESS"
        assert all(match_type == "FUZZY_ADDRESS" for _, match_type in results[1:])
        ids = [prop.id for prop, _ in results]
        assert len(ids) == len(set(ids)), "duplicate property ids across rungs"


class TestSearchEndpoint:
    def test_response_shape_and_string_uuid(self, client: TestClient, db: Session) -> None:
        target = make_property(db, "100 Test St", hcad="TEST000100")

        response = client.get("/api/v1/properties/search", params={"q": "100 test street"})

        assert response.status_code == 200
        results = response.json()["results"]
        assert results
        first = results[0]
        assert first == {
            "id": str(target.id),
            "address_line1": "100 Test St",
            "unit": None,
            "city": "Houston",
            "state": "TX",
            "postal_code": "77000",
            "hcad_account_id": "TEST000100",
            "match_type": "EXACT_ADDRESS",
        }

    def test_hcad_query_via_endpoint(self, client: TestClient, db: Session) -> None:
        target = make_property(db, "200 Test St", hcad="TEST000200")

        response = client.get("/api/v1/properties/search", params={"q": "TEST000200"})

        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["id"] == str(target.id)
        assert results[0]["match_type"] == "HCAD_ID"

    def test_missing_query_param_is_422(self, client: TestClient) -> None:
        response = client.get("/api/v1/properties/search")
        assert response.status_code == 422


def test_nul_byte_in_query_degrades_to_normal_miss(db: Session) -> None:
    prop = Property(
        address_line1="100 Test Street",
        city="Houston",
        state="TX",
        postal_code="77000",
        normalized_address=normalize_address("100 Test Street"),
        address_hash=address_hash(normalize_address("100 Test Street")),
    )
    db.add(prop)
    db.flush()
    # NUL bytes are stripped rather than bubbling up as a database error.
    results = search_properties(db, "100 Test\x00 Street")
    assert [p.id for p, _ in results] == [prop.id]
    assert search_properties(db, "\x00") == []
