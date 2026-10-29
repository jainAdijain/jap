from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, field_validator


class RunIn(BaseModel):
    """The Run dialog (T3): what to fetch and whether to apply. find_and_apply arrives with
    Apply (Phase 6)."""

    mode: Literal["find_only"] = "find_only"
    board_ids: list[UUID] | None = None  # None means every active board
    roles: list[str] | None = None  # None means every job; else only these roles' jobs

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        kept = list(dict.fromkeys(r.strip() for r in v if r.strip()))
        return kept or None


class RunOut(BaseModel):
    id: UUID
    trigger: str
    mode: str
    status: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    scope: dict[str, Any] = {}
    counts: dict[str, Any]
    error: str | None
    # Postings still waiting for extraction; the UI keeps polling while this is above 0.
    extracting: int = 0


class RunState(BaseModel):
    """The last run line (U6) and what is going now: the run in progress and the one queued
    behind it (T4 allows one of each)."""

    last: RunOut | None  # the newest finished run
    active: RunOut | None
    queued: RunOut | None
