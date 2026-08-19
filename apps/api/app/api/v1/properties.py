"""Properties API router.

This module establishes the `/api/v1/properties` router so `create_app()` wires a
real import from wave one. Wave two adds the endpoints defined in
plan/CONTRACTS.md: search, detail, timeline, findings, transactions, and sources.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/properties", tags=["properties"])
