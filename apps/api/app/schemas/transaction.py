"""Transaction-cycle response schemas. JSON field names are frozen per plan/CONTRACTS.md."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

UUIDStr = Annotated[uuid.UUID, PlainSerializer(str, return_type=str, when_used="always")]


class TransactionCycleOut(BaseModel):
    """One transaction arc (listing -> contract -> close/termination)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDStr
    started_at: datetime | None = None
    under_contract_at: datetime | None = None
    terminated_at: datetime | None = None
    closed_at: datetime | None = None
    outcome: str
    termination_reason: str
    reason_verification_level: str
