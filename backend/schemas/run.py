from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class RunIn(BaseModel):
    # find_and_apply arrives with Apply (Phase 6).
    mode: Literal["find_only"] = "find_only"
    board_ids: list[UUID] | None = None  # None means every active board


class RunOut(BaseModel):
    id: UUID
    trigger: str
    mode: str
    status: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    counts: dict[str, Any]
    error: str | None
    # Postings still waiting for extraction; the UI keeps polling while this is above 0.
    extracting: int = 0
