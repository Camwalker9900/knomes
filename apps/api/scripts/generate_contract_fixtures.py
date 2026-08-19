"""Generate the contract-parity fixtures in packages/shared/fixtures/ (plan Task D1.1).

Builds representative, fully deterministic Pydantic response objects IN MEMORY
(no database) for all six public response shapes, serializes them exactly the
way the API does (``model_dump(mode="json")``), and writes pretty-printed JSON:

    packages/shared/fixtures/search.json            {"results": [PropertySearchResult]}
    packages/shared/fixtures/property_detail.json   PropertyDetail
    packages/shared/fixtures/timeline.json          {"events": [TimelineEvent]}
    packages/shared/fixtures/findings.json          {"findings": [FindingDetail]}
    packages/shared/fixtures/transactions.json      {"transactions": [TransactionCycleOut]}
    packages/shared/fixtures/sources.json           {"sources": [SourceProvenance]}

Run from apps/api:

    uv run python scripts/generate_contract_fixtures.py

The output is byte-for-byte deterministic (fixed UUIDs and dates, no ``now()``,
no randomness): running it twice produces identical files. The backend suite
(tests/test_contract_fixtures.py) rebuilds the same objects via the builder
functions below and asserts byte equality with the committed files, so any
schema drift fails pytest. On the frontend, apps/web/src/lib/contracts.test.ts
parses the committed JSON with the client types.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
FIXTURES_DIR = REPO_ROOT / "packages" / "shared" / "fixtures"

# Allow running as a plain script (`python scripts/generate_contract_fixtures.py`),
# where sys.path[0] is scripts/ rather than the apps/api project root.
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.schemas import (  # noqa: E402
    ConditionSummary,
    FindingDetail,
    FreshnessEntry,
    PropertyDetail,
    PropertySearchResult,
    Provenance,
    ResolutionOut,
    SourceProvenance,
    TimelineEvent,
    TransactionCycleOut,
)

FIXTURE_NAMES = (
    "search",
    "property_detail",
    "timeline",
    "findings",
    "transactions",
    "sources",
)

# Fixed identifiers: stable, readable, never regenerated.
PROPERTY_100_ID = uuid.UUID("00000000-0000-4000-8000-000000000100")
PROPERTY_200_ID = uuid.UUID("00000000-0000-4000-8000-000000000200")
PROPERTY_300_ID = uuid.UUID("00000000-0000-4000-8000-000000000300")
EVENT_IDS = {n: uuid.UUID(f"00000000-0000-4000-8000-0000000002{n:02d}") for n in range(1, 7)}
FINDING_HVAC_ID = uuid.UUID("00000000-0000-4000-8000-000000000401")
FINDING_ROOF_ID = uuid.UUID("00000000-0000-4000-8000-000000000402")
RESOLUTION_ID = uuid.UUID("00000000-0000-4000-8000-000000000501")
TRANSACTION_TERMINATED_ID = uuid.UUID("00000000-0000-4000-8000-000000000601")
TRANSACTION_CLOSED_ID = uuid.UUID("00000000-0000-4000-8000-000000000602")

LAST_REFRESHED = "2026-01-15T08:30:00Z"


def _dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Fixed timezone-aware datetime (serializes as ISO-8601 with a Z suffix)."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def build_search_results() -> list[PropertySearchResult]:
    """All three match types; one result with a unit."""
    return [
        PropertySearchResult(
            id=PROPERTY_100_ID,
            address_line1="100 TEST ST",
            unit=None,
            city="HOUSTON",
            state="TX",
            postal_code="77000",
            hcad_account_id="TEST000100",
            match_type="EXACT_ADDRESS",
        ),
        PropertySearchResult(
            id=PROPERTY_200_ID,
            address_line1="200 TEST ST",
            unit=None,
            city="HOUSTON",
            state="TX",
            postal_code="77000",
            hcad_account_id="TEST000200",
            match_type="HCAD_ID",
        ),
        PropertySearchResult(
            id=PROPERTY_300_ID,
            address_line1="300 TEST ST",
            unit="APT 2",
            city="HOUSTON",
            state="TX",
            postal_code="77000",
            hcad_account_id=None,
            match_type="FUZZY_ADDRESS",
        ),
    ]


def build_property_detail() -> PropertyDetail:
    """Full detail including condition_summary and per-source freshness."""
    return PropertyDetail(
        id=PROPERTY_200_ID,
        address_line1="200 TEST ST",
        unit=None,
        city="HOUSTON",
        state="TX",
        postal_code="77000",
        hcad_account_id="TEST000200",
        year_built=1994,
        building_sqft=2150,
        lot_sqft=7405.0,
        property_type="SINGLE_FAMILY",
        latitude=29.7604,
        longitude=-95.3698,
        condition_summary=ConditionSummary(
            open_findings=1,
            resolved_findings=1,
            disputed_findings=0,
            verified_inspections=1,
            prior_transactions=2,
        ),
        freshness=[
            FreshnessEntry(source="synthetic_fixture", last_refreshed=LAST_REFRESHED),
            FreshnessEntry(source="houston_code_enforcement", last_refreshed=None),
        ],
    )


def build_timeline_events() -> list[TimelineEvent]:
    """Every provenance shape: with provenance, without, and SYSTEM_INFERRED w/ confidence."""
    return [
        TimelineEvent(
            id=EVENT_IDS[1],
            event_type="PROPERTY_CREATED",
            event_date=_dt(1994, 6, 1),
            title="Property record created",
            summary=None,
            verification_level="GOVERNMENT_RECORD",
            confidence=None,
            provenance=Provenance(source_name="synthetic_fixture", record_type="hcad_parcel"),
        ),
        TimelineEvent(
            id=EVENT_IDS[2],
            event_type="LISTING_ACTIVE",
            event_date=_dt(2025, 3, 10, 9),
            title="Listed for sale",
            summary=None,
            verification_level="TRANSACTION_DOCUMENT",
            confidence=None,
            provenance=Provenance(source_name="synthetic_fixture", record_type="listing"),
        ),
        TimelineEvent(
            id=EVENT_IDS[3],
            event_type="INSPECTION_PERFORMED",
            event_date=_dt(2025, 4, 2, 14, 30),
            title="General home inspection",
            summary="Licensed inspection covering roof, HVAC, plumbing, and electrical.",
            verification_level="LICENSED_PROFESSIONAL",
            confidence=None,
            provenance=Provenance(source_name="synthetic_fixture", record_type="inspection"),
        ),
        TimelineEvent(
            id=EVENT_IDS[4],
            event_type="CONDITION_FINDING",
            event_date=_dt(2025, 4, 2, 14, 30),
            title="HVAC condenser at end of service life",
            summary="Inspector reported the condenser unit is original and near failure.",
            verification_level="LICENSED_PROFESSIONAL",
            confidence=None,
            provenance=Provenance(source_name="synthetic_fixture", record_type="inspection"),
        ),
        TimelineEvent(
            id=EVENT_IDS[5],
            event_type="TRANSACTION_TERMINATED",
            event_date=_dt(2025, 4, 20),
            title="Contract terminated",
            summary="A previous contract appears not to have closed. Reason not verified.",
            verification_level="SYSTEM_INFERRED",
            confidence=0.7,
            provenance=None,
        ),
        TimelineEvent(
            id=EVENT_IDS[6],
            event_type="PERMIT_FINALIZED",
            event_date=_dt(2025, 6, 18, 16),
            title="Mechanical permit finalized",
            summary="HVAC replacement permit passed final inspection.",
            verification_level="GOVERNMENT_RECORD",
            confidence=None,
            provenance=Provenance(source_name="synthetic_fixture", record_type="permit"),
        ),
    ]


def build_findings() -> list[FindingDetail]:
    """One resolved finding (with a resolution) and one open finding (empty resolutions)."""
    return [
        FindingDetail(
            id=FINDING_HVAC_ID,
            category="HVAC",
            subcategory="CONDENSER",
            title="HVAC condenser at end of service life",
            description="Condenser unit is original to the house and near failure.",
            severity="MAJOR",
            status="RESOLVED",
            first_observed_at=_dt(2025, 4, 2, 14, 30),
            latest_observed_at=_dt(2025, 4, 2, 14, 30),
            resolutions=[
                ResolutionOut(
                    id=RESOLUTION_ID,
                    resolution_type="REPLACEMENT",
                    description="Condenser replaced under a finalized mechanical permit.",
                    verification_level="DOCUMENT_SUPPORTED",
                    resolved_at=_dt(2025, 6, 18, 16),
                    event_id=EVENT_IDS[6],
                ),
            ],
        ),
        FindingDetail(
            id=FINDING_ROOF_ID,
            category="ROOF",
            subcategory=None,
            title="Composition shingles showing wear",
            description=None,
            severity="MODERATE",
            status="OPEN",
            first_observed_at=_dt(2025, 4, 2, 14, 30),
            latest_observed_at=None,
            resolutions=[],
        ),
    ]


def build_transactions() -> list[TransactionCycleOut]:
    """A terminated cycle with UNKNOWN reason (SYSTEM_INFERRED) and a closed cycle."""
    return [
        TransactionCycleOut(
            id=TRANSACTION_TERMINATED_ID,
            started_at=_dt(2025, 3, 10, 9),
            under_contract_at=_dt(2025, 3, 28),
            terminated_at=_dt(2025, 4, 20),
            closed_at=None,
            outcome="TERMINATED",
            termination_reason="UNKNOWN",
            reason_verification_level="SYSTEM_INFERRED",
        ),
        TransactionCycleOut(
            id=TRANSACTION_CLOSED_ID,
            started_at=_dt(2025, 5, 5, 9),
            under_contract_at=_dt(2025, 5, 30),
            terminated_at=None,
            closed_at=_dt(2025, 7, 1),
            outcome="CLOSED",
            termination_reason="UNKNOWN",
            reason_verification_level="UNVERIFIED",
        ),
    ]


def build_sources() -> list[SourceProvenance]:
    """One refreshed source and one never-refreshed source."""
    return [
        SourceProvenance(
            source="synthetic_fixture",
            record_count=12,
            last_refreshed=LAST_REFRESHED,
        ),
        SourceProvenance(
            source="houston_code_enforcement",
            record_count=0,
            last_refreshed=None,
        ),
    ]


def build_fixture_payloads() -> dict[str, Any]:
    """All six fixture payloads as JSON-ready dicts, keyed by fixture file stem.

    List endpoints are wrapped in the exact envelope the API returns
    (``results`` / ``events`` / ``findings`` / ``transactions`` / ``sources``);
    property_detail is the bare object, as returned by GET /api/v1/properties/{id}.
    """
    return {
        "search": {"results": [m.model_dump(mode="json") for m in build_search_results()]},
        "property_detail": build_property_detail().model_dump(mode="json"),
        "timeline": {"events": [m.model_dump(mode="json") for m in build_timeline_events()]},
        "findings": {"findings": [m.model_dump(mode="json") for m in build_findings()]},
        "transactions": {
            "transactions": [m.model_dump(mode="json") for m in build_transactions()]
        },
        "sources": {"sources": [m.model_dump(mode="json") for m in build_sources()]},
    }


def render(payload: dict[str, Any]) -> str:
    """Canonical on-disk representation: pretty JSON, trailing newline."""
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    payloads = build_fixture_payloads()
    assert tuple(payloads) == FIXTURE_NAMES
    for name in FIXTURE_NAMES:
        path = FIXTURES_DIR / f"{name}.json"
        path.write_text(render(payloads[name]), encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
