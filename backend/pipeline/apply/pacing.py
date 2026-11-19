"""Pacing per job board, so no ATS rate-limits or flags me (A1, V2 to V5, PRD 1.29).

Automatic mode sends 10 or more applications a day. Sent in a burst, newest first, they
would land on a few companies and one or two ATS hosts at once, which is what a board's
abuse checks look for. So:

- pick takes the matching jobs in random order (V2), at most JAP_PER_COMPANY_DAY attempts a
  day at one company (V3) and at most JAP_PER_ATS_DAY a day on one ATS (V4);
- submit waits at least JAP_ATS_GAP_SECONDS after the last submit on the same ATS (V4), on
  top of the global gap plus a jitter that differs per job (A1);
- a 429 or 403 from an ATS, or a page saying too many requests, puts that ATS on hold for 1,
  then 4, then 24 hours (V5). Nothing is read or submitted there until the hold ends, and the
  job waits instead of failing. A confirmed submit on that ATS clears the strikes.

Days are counted from local midnight in JAP_TZ, like the daily cap.
"""

import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import Application, AtsHold, Listing, Posting
from observability.logs import event

log = logging.getLogger(__name__)

HOLDS = (timedelta(hours=1), timedelta(hours=4), timedelta(hours=24))

# "The form page answered 429" (form._open_page), or what a board prints when it throttles.
BLOCKED = re.compile(
    r"\banswered (429|403)\b|too many requests|rate.?limit|temporarily blocked|"
    r"unusual (traffic|activity)|access denied",
    re.IGNORECASE,
)


def is_blocked(message: str | None) -> bool:
    """Whether an error says the board throttled or refused us, rather than a form problem."""
    return bool(message and BLOCKED.search(message))


def local_midnight(now: datetime) -> datetime:
    local = now.astimezone(ZoneInfo(get_settings().jap_tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def jitter(app_id: str | uuid.UUID) -> timedelta:
    """A random-looking 0 to JAP_APPLY_JITTER_SECONDS that stays the same for one job, so a
    submit step woken from its wait does not draw a new, longer wait every time."""
    top = max(0, get_settings().jap_apply_jitter_seconds)
    n = int(hashlib.sha256(str(app_id).encode()).hexdigest()[:8], 16)
    return timedelta(seconds=n % (top + 1))


async def ats_of(session: AsyncSession, app: Application) -> str | None:
    return await session.scalar(select(Posting.source).where(Posting.id == app.posting_id))


async def hold_until(session: AsyncSession, ats: str | None, now: datetime) -> datetime | None:
    """When the hold on this ATS ends, or None when it is not on hold."""
    if ats is None:
        return None
    hold = await session.get(AtsHold, ats)
    if hold is None or hold.until <= now:
        return None
    return hold.until


async def held_boards(session: AsyncSession, now: datetime) -> dict[str, datetime]:
    rows = await session.scalars(select(AtsHold).where(AtsHold.until > now))
    return {h.ats: h.until for h in rows}


async def record_block(session: AsyncSession, ats: str | None, reason: str) -> datetime:
    """Put the ATS on hold after a 429 or 403 (V5) and return when the hold ends. The caller
    commits."""
    now = datetime.now(UTC)
    if ats is None:
        return now + HOLDS[0]
    hold = await session.get(AtsHold, ats, with_for_update=True)
    if hold is None:
        hold = AtsHold(ats=ats, until=now, strikes=0)
        session.add(hold)
    if hold.until > now:  # already on hold: a second job hit it before it ended
        return hold.until
    hold.strikes = int(hold.strikes or 0) + 1
    hold.until = now + HOLDS[min(hold.strikes, len(HOLDS)) - 1]
    hold.reason = reason[:300]
    hold.updated_at = now
    event(log, "apply.board_hold", f"{ats} is on hold until {hold.until.isoformat()}: {reason}",
          level=logging.WARNING, keep=True, ats=ats, strikes=hold.strikes)  # fmt: skip
    return hold.until


async def clear_strikes(session: AsyncSession, ats: str | None) -> None:
    """A confirmed submit on this ATS: the next block starts again at 1 hour."""
    if ats is None:
        return
    hold = await session.get(AtsHold, ats)
    if hold is not None and hold.strikes:
        hold.strikes = 0


async def submitted_on(session: AsyncSession, ats: str, since: datetime) -> int:
    n = await session.scalar(
        select(func.count())
        .select_from(Application)
        .join(Posting, Posting.id == Application.posting_id)
        .where(Posting.source == ats, Application.submitted_at >= since)
    )
    return int(n or 0)


async def ats_wait(session: AsyncSession, ats: str | None, now: datetime) -> datetime | None:
    """V4 and V5 for one submit: when it may go on this ATS, or None to go now."""
    if ats is None:
        return None
    settings = get_settings()
    held = await hold_until(session, ats, now)
    if held is not None:
        return held
    midnight = local_midnight(now)
    if await submitted_on(session, ats, midnight) >= settings.jap_per_ats_day:
        return midnight + timedelta(days=1, minutes=2)
    last: datetime | None = await session.scalar(
        select(func.max(Application.submitted_at))
        .join(Posting, Posting.id == Application.posting_id)
        .where(Posting.source == ats)
    )
    gap = timedelta(seconds=settings.jap_ats_gap_seconds)
    if last is not None and now - last < gap:
        return last + gap
    return None


async def started_today(session: AsyncSession, now: datetime) -> dict[str, dict[str, int]]:
    """Attempts started or sent today, by company id and by ATS (V3, V4): every attempt
    counts, sent or not, except one I cancelled."""
    midnight = local_midnight(now)
    rows = await session.execute(
        select(Listing.company_id, Posting.source)
        .select_from(Application)
        .join(Listing, Listing.id == Application.listing_id)
        .join(Posting, Posting.id == Application.posting_id)
        .where(
            Application.status != "cancelled",
            or_(Application.created_at >= midnight, Application.submitted_at >= midnight),
        )
    )
    companies: dict[str, int] = {}
    boards: dict[str, int] = {}
    for company_id, ats in rows.all():
        if company_id is not None:
            companies[str(company_id)] = companies.get(str(company_id), 0) + 1
        boards[str(ats)] = boards.get(str(ats), 0) + 1
    return {"companies": companies, "boards": boards}


async def in_flight(session: AsyncSession, now: datetime) -> int:
    """Automatic attempts still on their way to a submit (V7): preparing or applying, or
    waiting at Needs you since today (the Claude writer has not answered them yet)."""
    midnight = local_midnight(now)
    n = await session.scalar(
        select(func.count())
        .select_from(Application)
        .where(
            Application.mode == "automatic",
            or_(
                Application.status.in_(("preparing", "applying")),
                (Application.status == "needs_input") & (Application.created_at >= midnight),
            ),
        )
    )
    return int(n or 0)
