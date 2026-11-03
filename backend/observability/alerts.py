"""Health alert emails (PRD O6), checked every hour by health-watch.

An email goes to the address on My profile when:
- the Daily search didn't start (half an hour after its time)
- a search has been stuck for over 30 minutes
- more errors than JAP_ALERT_ERRORS_PER_HOUR happened in the last hour
- AI spend reached 80% or 100% of this month's cap
- the disk that holds data/ is more than 85% full
Failed searches, failed applications and failed form tests already have their own alerts
(pipeline/alerts.py). Each alert has a key that includes the day (or the month for spend, or
the run for a stuck search), and alert_state remembers it was sent, so one problem sends at
most one email a day. JAP_ALERTS_ENABLED=false (dev, test) sends nothing.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import AlertState, LogEvent, Profile, RouteStat
from llm.spend import month_spend
from observability import status as health
from pipeline.notify import EmailNotSent, Mail

DISK_LIMIT_PCT = 85.0
KEEP_LOGS = timedelta(days=30)


@dataclass
class Alert:
    key: str
    subject: str
    lines: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ui() -> str:
    return get_settings().jap_ui_url.rstrip("/") + "/#settings"


async def due(session: AsyncSession, now: datetime | None = None) -> list[Alert]:
    """The alerts that apply now and were not sent yet under their key."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(settings.jap_tz))
    day, month = local.strftime("%Y-%m-%d"), local.strftime("%Y-%m")
    found: list[Alert] = []

    daily = await health.daily_search(session, now)
    if daily.missed:
        found.append(
            Alert(
                f"daily-missed:{day}",
                "JAP alert: today's Daily search didn't start",
                [
                    f"The Daily search is set for {daily.at} ({settings.jap_tz}) and has not "
                    "started today.",
                    "The Mac may have been asleep, or Docker or Inngest may not be running.",
                ],
            )
        )
    for run in await health.stuck_runs(session, now):
        minutes = int((now - run.started_at).total_seconds() // 60)
        found.append(
            Alert(
                f"stuck-run:{run.id}",
                "JAP alert: a search is stuck",
                [
                    f"A search started {minutes} minutes ago is still {run.status}.",
                    "Check the Inngest dashboard and the app logs (make logs).",
                ],
            )
        )
    errors = await health.error_count(session, now - timedelta(hours=1))
    if errors > settings.jap_alert_errors_per_hour:
        groups = await health.error_groups(session, now - timedelta(hours=1), limit=5)
        found.append(
            Alert(
                f"errors:{day}",
                f"JAP alert: {errors} errors in the last hour",
                [f"JAP logged {errors} errors in the last hour. The most common:", ""]
                + [f"- {g.event} ({g.count}): {g.last_message[:200]}" for g in groups],
            )
        )
    spend = await month_spend(session)
    pct = 100 * spend.month_usd / spend.cap_usd if spend.cap_usd else 100.0
    if pct >= 100:
        found.append(
            Alert(
                f"spend-100:{month}",
                "JAP alert: the AI spend limit is reached",
                [
                    f"${spend.month_usd:.2f} of ${spend.cap_usd:.2f} is used this month.",
                    "JAP has stopped reading descriptions and writing answers and letters until "
                    "next month or until the limit is raised in Settings.",
                ],
            )
        )
    elif pct >= 80:
        found.append(
            Alert(
                f"spend-80:{month}",
                f"JAP alert: {pct:.0f}% of this month's AI limit is used",
                [f"${spend.month_usd:.2f} of ${spend.cap_usd:.2f} is used this month."],
            )
        )
    space = health.disk()
    if space.used_pct > DISK_LIMIT_PCT:
        found.append(
            Alert(
                f"disk:{day}",
                f"JAP alert: the disk is {space.used_pct:.0f}% full",
                [
                    f"The disk that holds JAP's data is {space.used_pct:.0f}% full "
                    f"({space.free_bytes // 2**30} GB free).",
                    f"data/ uses {space.data_bytes // 2**20} MB, of which backups "
                    f"{space.backups_bytes // 2**20} MB.",
                ],
            )
        )
    if not found:
        return []
    sent = set(
        await session.scalars(
            select(AlertState.key).where(AlertState.key.in_([a.key for a in found]))
        )
    )
    return [a for a in found if a.key not in sent]


async def mail_for(session: AsyncSession, alert: Alert) -> Mail:
    email = await session.scalar(select(Profile.email))
    if not email:
        raise EmailNotSent("My profile has no email address")
    env = get_settings().jap_env
    subject = alert.subject if env == "prod" else f"[{env.upper()}] {alert.subject}"
    body = "\n".join([*alert.lines, "", f"Status in JAP: {_ui()}"]) + "\n"
    return Mail(to=str(email), subject=subject, body=body)


async def already_sent(session: AsyncSession, key: str) -> bool:
    return await session.get(AlertState, key) is not None


async def mark_sent(session: AsyncSession, alert: Alert, now: datetime | None = None) -> None:
    stmt = insert(AlertState).values(
        key=alert.key, last_sent_at=now or datetime.now(UTC), detail=alert.subject
    )
    await session.execute(stmt.on_conflict_do_nothing(index_elements=["key"]))
    await session.commit()


async def cleanup(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Delete log events and route timings past 30 days, and alert keys past 90 (O3)."""
    now = now or datetime.now(UTC)
    logs = await session.execute(delete(LogEvent).where(LogEvent.created_at < now - KEEP_LOGS))
    routes = await session.execute(delete(RouteStat).where(RouteStat.hour < now - KEEP_LOGS))
    keys = await session.execute(
        delete(AlertState).where(AlertState.last_sent_at < now - timedelta(days=90))
    )
    await session.commit()
    return {
        "log_events": int(getattr(logs, "rowcount", 0) or 0),
        "route_stats": int(getattr(routes, "rowcount", 0) or 0),
        "alert_keys": int(getattr(keys, "rowcount", 0) or 0),
    }
