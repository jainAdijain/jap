"""Settings > Status (PRD O5), Insights (O10 to O12) and the application timeline (O8)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from schemas.profile import Spend


class RunBrief(BaseModel):
    id: UUID
    trigger: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    error: str | None


class DailySearch(BaseModel):
    on: bool
    at: str | None  # HH:MM in JAP_TZ
    last: RunBrief | None
    missed: bool  # today's time passed with no scheduled run


class ErrorGroup(BaseModel):
    event: str
    count: int
    last_at: datetime
    last_message: str
    application_id: UUID | None
    request_id: str | None


class RouteOut(BaseModel):
    method: str
    route: str
    count: int
    errors: int
    avg_ms: int
    max_ms: int


class Disk(BaseModel):
    data_bytes: int
    backups_bytes: int
    free_bytes: int
    total_bytes: int
    used_pct: float


class AlertOut(BaseModel):
    key: str
    sent_at: datetime
    detail: str | None


class Status(BaseModel):
    checked_at: datetime
    env: str
    daily: DailySearch
    stuck_runs: list[RunBrief]
    errors_24h: int
    error_groups: list[ErrorGroup]
    slow_routes: list[RouteOut]
    disk: Disk
    spend: Spend
    alerts: list[AlertOut]


class FunnelWeek(BaseModel):
    week: str  # Monday of the week, YYYY-MM-DD in JAP_TZ
    found: int  # new jobs (listings) first seen
    matched: int  # of those, ones that match My preferences now
    prepared: int  # applications started
    applied: int  # applications sent and confirmed by the site
    failed: int  # applications that ended Failed


class SourceRow(BaseModel):
    source: str  # greenhouse, lever or ashby
    postings: int
    matched: int
    applied: int
    failed: int
    success_rate: float | None  # applied / (applied + failed); None before any outcome
    top_failures: list[str]


class SpendRow(BaseModel):
    purpose: str
    model: str
    calls: int
    cost_usd: float


class MonthSpend(BaseModel):
    month: str  # YYYY-MM
    cost_usd: float
    applied: int
    per_application_usd: float | None


class Insights(BaseModel):
    weeks: int
    funnel: list[FunnelWeek]
    totals: FunnelWeek
    sources: list[SourceRow]
    spend_this_month: list[SpendRow]
    months: list[MonthSpend]


class TimelineItem(BaseModel):
    at: datetime
    kind: Literal["step", "model", "status"]
    event: str
    text: str
    level: str
    duration_ms: int | None = None
    cost_usd: float | None = None
    fields: dict[str, Any] = {}


class Timeline(BaseModel):
    application_id: UUID
    status: str
    items: list[TimelineItem]
    kept_days: int
