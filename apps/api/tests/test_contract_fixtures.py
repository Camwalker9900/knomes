"""Contract-parity fixtures (plan Task D1.1) stay in lockstep with the schemas.

Rebuilds the exact fixture payloads via the builder functions in
scripts/generate_contract_fixtures.py and asserts byte equality with the JSON
committed to packages/shared/fixtures/. Any drift in the Pydantic response
schemas (renamed field, changed serialization, nullability change) fails here;
the frontend parses the same files in apps/web/src/lib/contracts.test.ts.

No database required. Regenerate after intentional schema changes with:

    cd apps/api && uv run python scripts/generate_contract_fixtures.py
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.generate_contract_fixtures import (
    FIXTURE_NAMES,
    FIXTURES_DIR,
    build_fixture_payloads,
    render,
)

REGEN_HINT = "regenerate with: cd apps/api && uv run python scripts/generate_contract_fixtures.py"


@pytest.fixture(scope="module")
def payloads() -> dict[str, Any]:
    return build_fixture_payloads()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_committed_fixture_matches_current_schema(name: str, payloads: dict[str, Any]) -> None:
    path = FIXTURES_DIR / f"{name}.json"
    assert path.is_file(), f"missing fixture {path}; {REGEN_HINT}"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == payloads[name], f"{name}.json drifted from the response schemas; {REGEN_HINT}"
    # Byte-for-byte: catches hand-edits and formatting drift, keeps output deterministic.
    assert path.read_text(encoding="utf-8") == render(payloads[name]), (
        f"{name}.json formatting differs from the generator output; {REGEN_HINT}"
    )


def test_fixture_dir_has_no_strays() -> None:
    committed = {p.name for p in FIXTURES_DIR.glob("*.json")}
    assert committed == {f"{name}.json" for name in FIXTURE_NAMES}


def test_fixtures_cover_required_contract_cases(payloads: dict[str, Any]) -> None:
    """The representative cases the plan calls out are actually present."""
    events = payloads["timeline"]["events"]
    inferred = [e for e in events if e["verification_level"] == "SYSTEM_INFERRED"]
    assert inferred and all(
        isinstance(e["confidence"], float) for e in inferred
    ), "timeline must include a SYSTEM_INFERRED event with confidence"
    assert any(e["provenance"] is not None for e in events)

    assert any(f["resolutions"] for f in payloads["findings"]["findings"])

    cycles = payloads["transactions"]["transactions"]
    assert any(
        c["termination_reason"] == "UNKNOWN"
        and c["reason_verification_level"] == "SYSTEM_INFERRED"
        for c in cycles
    )

    detail = payloads["property_detail"]
    assert set(detail["condition_summary"]) == {
        "open_findings",
        "resolved_findings",
        "disputed_findings",
        "verified_inspections",
        "prior_transactions",
    }
    assert detail["freshness"], "property_detail must include per-source freshness"

    assert {s["source"] for s in payloads["sources"]["sources"]}
