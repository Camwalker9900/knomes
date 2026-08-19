"""Spec §50 cases for the deterministic address normalizer and hash."""

import pytest

from app.lib.address import address_hash, normalize_address


@pytest.mark.parametrize(
    "raw",
    ["9219 Timberside Dr.", "9219 TIMBERSIDE DRIVE", "9219 Timberside Dr"],
)
def test_timberside_variants_normalize_identically(raw: str) -> None:
    assert normalize_address(raw) == "9219 TIMBERSIDE DR"


@pytest.mark.parametrize(
    "raw",
    ["100 test street", "100 Test St.", "100 TEST ST"],
)
def test_test_street_variants(raw: str) -> None:
    assert normalize_address(raw) == "100 TEST ST"


def test_directional_word_abbreviated() -> None:
    assert normalize_address("1200 North Main Street") == "1200 N MAIN ST"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1200 N Main St", "1200 N MAIN ST"),
        ("500 Southwest Oak Lane", "500 SW OAK LN"),
    ],
)
def test_directionals(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["100 Test St #4B", "100 Test St Unit 4B", "100 Test St Apt 4B"],
)
def test_unit_designators_canonicalize_to_apt(raw: str) -> None:
    assert normalize_address(raw) == "100 TEST ST APT 4B"


@pytest.mark.parametrize(
    "raw",
    ["100 Test St # 4B", "100 Test St Apt #4B", "100 Test St apartment 4B"],
)
def test_unit_designator_edge_forms(raw: str) -> None:
    assert normalize_address(raw) == "100 TEST ST APT 4B"


@pytest.mark.parametrize(
    "raw",
    ["100 Test St Suite 210", "100 Test St Ste 210", "100 Test St STE 210"],
)
def test_suite_stays_ste(raw: str) -> None:
    assert normalize_address(raw) == "100 TEST ST STE 210"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9219 Peñasco Dr", "9219 PENASCO DR"),
        ("100 Café Boulevard", "100 CAFE BLVD"),
        ("400 Ångström Way", "400 ANGSTROM WAY"),
    ],
)
def test_accents_fold_to_ascii(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("425 1st Street", "425 1ST ST"),
        ("2500 W 43rd Avenue", "2500 W 43RD AVE"),
        ("100 2ND ST", "100 2ND ST"),
    ],
)
def test_ordinal_streets_preserved(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  100   Test    Street  ", "100 TEST ST"),
        ("100 Test Street,", "100 TEST ST"),
        ("100 Test Street.", "100 TEST ST"),
        ("100, Test Street", "100 TEST ST"),
    ],
)
def test_whitespace_and_punctuation(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 Oak Avenue", "1 OAK AVE"),
        ("1 Oak Boulevard", "1 OAK BLVD"),
        ("1 Oak Lane", "1 OAK LN"),
        ("1 Oak Court", "1 OAK CT"),
        ("1 Oak Road", "1 OAK RD"),
        ("1 Oak Parkway", "1 OAK PKWY"),
        ("1 Oak Highway", "1 OAK HWY"),
        ("1 Oak Circle", "1 OAK CIR"),
        ("1 Oak Place", "1 OAK PL"),
        ("1 Oak Trail", "1 OAK TRL"),
        ("1 Oak Way", "1 OAK WAY"),
        ("1 Oak Terrace", "1 OAK TER"),
        ("1 Oak Square", "1 OAK SQ"),
        ("1 Oak Loop", "1 OAK LOOP"),
        ("1 Oak Cove", "1 OAK CV"),
        ("1 Oak Bend", "1 OAK BND"),
    ],
)
def test_suffix_table(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected


def test_hash_is_sha256_hex() -> None:
    digest = address_hash("9219 TIMBERSIDE DR")
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)  # raises if not hex


def test_hash_deterministic() -> None:
    assert address_hash("9219 TIMBERSIDE DR") == address_hash("9219 TIMBERSIDE DR")


def test_hash_differs_for_different_inputs() -> None:
    assert address_hash("9219 TIMBERSIDE DR") != address_hash("100 TEST ST")


def test_equivalent_raw_forms_hash_identically() -> None:
    a = address_hash(normalize_address("9219 Timberside Dr."))
    b = address_hash(normalize_address("9219 TIMBERSIDE DRIVE"))
    assert a == b
