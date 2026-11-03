"""What Settings > Status shows (PRD O5): is JAP healthy right now?

- the Daily search: when it last ran, and whether today's is missing
- searches stuck for over 30 minutes
- errors in the last 24 hours, grouped by event
- the slowest API routes in the last 24 hours
- disk used by data/ and its backups, and free space on that disk
- this month's AI spend against the cap
- the health alerts emailed lately
The last backup and form test come from GET /api/ops as before.
"""

import os
import shutil
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import AlertState, LogEvent, Profile, RouteStat, Run
from llm.spend import month_spend
from schemas.status import (
    AlertOut,
    DailySearch,
    Disk,
    ErrorGroup,
    RouteOut,
    RunBrief,
    Status,
)

STUCK_AFTER = timedelta(minutes=30)
DAILY_GRACE = timedelta(minutes=30)


def _size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def disk() -> Disk:
    data = Path(get_settings().jap_data_dir)
    if not data.exists():
        return Disk(data_bytes=0, backups_bytes=0, free_bytes=0, total_bytes=0, used_pct=0.0)
    usage = shutil.disk_usage(data)
    return Disk(
        data_bytes=_size(data),
        backups_bytes=_size(data / "backups"),
        free_bytes=usage.free,
        total_bytes=usage.total,
        used_pct=round(100 * usage.used / usage.total, 1) if usage.total else 0.0,
    )


def _brief(run: Run) -> RunBrief:
    return RunBrief(
        id=run.id,
        trigger=run.trigger,
        status=run.status,
        started_at=run.started_at or run.created_at,
        ended_at=run.ended_at,
        error=run.error,
    )


async def daily_search(session: AsyncSession, now: datetime) -> DailySearch:
    """The Daily search: on or off, its last run, and missed=True when today's time passed
    (plus half an hour) with no scheduled run since."""
    tz = ZoneInfo(get_settings().jap_tz)
    profile = await session.scalar(select(Profile))
    last = await session.scalar(
        select(Run).where(Run.trigger == "scheduled").order_by(desc(Run.created_at)).limit(1)
    )
    if profile is None or not profile.daily_run:
        return DailySearch(on=False, at=None, last=_brief(last) if last else None, missed=False)
    at: time = profile.daily_run_at
    local = now.astimezone(tz)
    due = datetime.combine(local.date(), at, tzinfo=tz)
    missed = False
    if local >= due + DAILY_GRACE:
        ran = last is not None and last.created_at >= due.astimezone(UTC) - timedelta(minutes=5)
        # Turned on after today's time: nothing was due yet.
        switched_on_late = profile.updated_at.astimezone(tz) > due
        missed = not ran and not switched_on_late
    return DailySearch(
        on=True, at=at.strftime("%H:%M"), last=_brief(last) if last else None, missed=missed
    )


async def stuck_runs(session: AsyncSession, now: datetime) -> list[RunBrief]:
    rows = await session.scalars(
        select(Run)
        .where(Run.status.in_(("queued", "running")))
        .where(func.coalesce(Run.started_at, Run.created_at) < now - STUCK_AFTER)
        .order_by(Run.created_at)
    )
    return [_brief(r) for r in rows]


async def error_groups(session: AsyncSession, since: datetime, limit: int = 20) -> list[ErrorGroup]:
    """Errors since then, one row per event: how many, and the latest message."""
    latest = (
        select(
            LogEvent.event,
            LogEvent.message,
            LogEvent.created_at,
            LogEvent.application_id,
            LogEvent.request_id,
            func.count().over(partition_by=LogEvent.event).label("n"),
            func.row_number()
            .over(partition_by=LogEvent.event, order_by=desc(LogEvent.created_at))
            .label("rn"),
        )
        .where(LogEvent.level.in_(("error", "critical")))
        .where(LogEvent.created_at >= since)
        .subquery()
    )
    rows = await session.execute(
        select(latest).where(latest.c.rn == 1).order_by(desc(latest.c.n)).limit(limit)
    )
    return [
        ErrorGroup(
            event=r.event,
            count=r.n,
            last_at=r.created_at,
            last_message=r.message,
            application_id=r.application_id,
            request_id=r.request_id,
        )
        for r in rows
    ]


async def error_count(session: AsyncSession, since: datetime) -> int:
    n = await session.scalar(
        select(func.count())
        .select_from(LogEvent)
        .where(LogEvent.level.in_(("error", "critical")))
        .where(LogEvent.created_at >= since)
    )
    return int(n or 0)


async def slow_routes(session: AsyncSession, since: datetime, limit: int = 8) -> list[RouteOut]:
    count = func.sum(RouteStat.count)
    rows = await session.execute(
        select(
            RouteStat.method,
            RouteStat.route,
            count.label("n"),
            func.sum(RouteStat.errors).label("errors"),
            (func.sum(RouteStat.total_ms) / func.nullif(count, 0)).label("avg_ms"),
            func.max(RouteStat.max_ms).label("max_ms"),
        )
        .where(RouteStat.hour >= since.replace(minute=0, second=0, microsecond=0))
        .group_by(RouteStat.method, RouteStat.route)
        .order_by(desc("avg_ms"))
        .limit(limit)
    )
    return [
        RouteOut(
            method=r.method,
            route=r.route,
            count=int(r.n),
            errors=int(r.errors),
            avg_ms=int(r.avg_ms or 0),
            max_ms=int(r.max_ms),
        )
        for r in rows
    ]


async def recent_alerts(session: AsyncSession, since: datetime) -> list[AlertOut]:
    rows = await session.scalars(
        select(AlertState)
        .where(AlertState.last_sent_at >= since)
        .order_by(desc(AlertState.last_sent_at))
    )
    return [AlertOut(key=r.key, sent_at=r.last_sent_at, detail=r.detail) for r in rows]


async def status(session: AsyncSession, now: datetime | None = None) -> Status:
    now = now or datetime.now(UTC)
    day = now - timedelta(hours=24)
    return Status(
        checked_at=now,
        env=get_settings().jap_env,
        daily=await daily_search(session, now),
        stuck_runs=await stuck_runs(session, now),
        errors_24h=await error_count(session, day),
        error_groups=await error_groups(session, day),
        slow_routes=await slow_routes(session, day),
        disk=disk(),
        spend=await month_spend(session),
        alerts=await recent_alerts(session, now - timedelta(days=7)),
    )
