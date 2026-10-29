"""What starts a run besides the Run dialog (T1, T2, Triggers): a profile save and the
Daily run. An accepted resume starts its run from the API (api/resumes.py).

profile-saved: the save has already re-filtered the stored listings (T2, in PUT
/api/profile); this fetches in the background so new matching jobs follow. Debounced, so a
burst of saves makes one run, and a run already queued is reused (T4).

daily-schedule: ticks every minute in JAP_TZ and fires when the Daily run is on and its
time (06:00 unless changed) has come. A tick is due only in the first few minutes after
that time, so a slot missed while the Mac slept is skipped, not caught up (accepted for v1).
In Review mode it finds jobs only; in Automatic mode it finds and applies (Phase 8), within
the daily cap, so the morning run applies without me.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import inngest
from sqlalchemy import select

from config import get_settings
from database.models import Profile
from database.session import get_sessionmaker
from functions.client import inngest_client
from pipeline.runs import queue_run

DUE_WINDOW = timedelta(minutes=5)


def daily_due(now: datetime, at: time, last_fired: datetime | None) -> bool:
    """Whether the Daily run fires now: within DUE_WINDOW after today's slot (in now's
    zone), and not fired since that slot."""
    slot = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if not slot <= now < slot + DUE_WINDOW:
        return False
    return last_fired is None or last_fired < slot


async def queue_step(trigger: str) -> str | None:
    """Queue a find only run; the run id when it is new and needs its event sent."""
    async with get_sessionmaker()() as session:
        run, created = await queue_run(session, trigger, "find_only")
    return str(run.id) if created else None


async def daily_step() -> str | None:
    """Fire the Daily run when it is due: the run id to send, or None."""
    tz = ZoneInfo(get_settings().jap_tz)
    async with get_sessionmaker()() as session:
        profile = await session.scalar(select(Profile))
        if profile is None or not profile.daily_run:
            return None
        now = datetime.now(tz)
        last = profile.daily_run_last_at.astimezone(tz) if profile.daily_run_last_at else None
        if not daily_due(now, profile.daily_run_at, last):
            return None
        profile.daily_run_last_at = datetime.now(UTC)
        mode = "find_and_apply" if profile.apply_mode == "automatic" else "find_only"
        await session.commit()
        run, created = await queue_run(session, "scheduled", mode)
    return str(run.id) if created else None


async def _send(ctx: inngest.Context, run_id: str | None) -> str | None:
    if run_id is not None:
        await ctx.step.send_event(
            "send-run", inngest.Event(name="run/requested", data={"run_id": run_id})
        )
    return run_id


@inngest_client.create_function(
    fn_id="profile-saved",
    trigger=inngest.TriggerEvent(event="profile/saved"),
    debounce=inngest.Debounce(period=timedelta(seconds=2)),
    retries=2,
)
async def profile_saved(ctx: inngest.Context) -> str | None:
    return await _send(ctx, await ctx.step.run("queue-run", queue_step, "profile_saved"))


@inngest_client.create_function(
    fn_id="daily-schedule",
    trigger=inngest.TriggerCron(cron=f"TZ={get_settings().jap_tz} * * * * *"),
    retries=0,  # the next tick is a minute away
)
async def daily_schedule(ctx: inngest.Context) -> str | None:
    return await _send(ctx, await ctx.step.run("check", daily_step))
