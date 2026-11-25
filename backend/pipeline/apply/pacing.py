"""Pacing per job board, so no ATS rate-limits or flags me (A1, V2 to V5, PRD 1.29).

Automatic mode sends 10 or more applications a day. Sent in a burst, newest first, they
would land on a few companies and one or two ATS hosts at once, which is what a board's
abuse checks look for. So:

- pick takes the matching jobs in random order (V2), at most JAP_PER_COMPANY_DAY attempts a
  day at one company (V3) and at most JAP_PER_ATS_DAY a day on one ATS (V4);
- submit waits at least JAP_ATS_GAP_SECONDS after the last submit on the same ATS (V4), on
  top of the global gap plus a jitter that differs per job (A1);
- a company that caps applications from one person, as its form says (Supabase: 3 in 60
  days), gets no submit and no pick past that cap until the window has room (JAP-13);
- a 429 or 403 from an ATS, or a page saying too many requests, puts that ATS on hold for 1,
  then 4, then 24 hours (V5). Nothing is read or submitted there until the hold ends, and the
  job waits instead of failing. A confirmed submit on that ATS clears the strikes;
- the same refusal from a company's own careers site, where some companies on Greenhouse
  send applicants, holds that company only, so one site that refuses the browser does not
  stop every other company on the board (PRD 1.39). The page's address says which: the
  board's own domain, or anywhere else.

Days are counted from local midnight in JAP_TZ, like the daily cap.
"""

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import Application, AtsHold, Company, CompanyHold, Listing, Posting
from observability.logs import event

log = logging.getLogger(__name__)

HOLDS = (timedelta(hours=1), timedelta(hours=4), timedelta(hours=24))

# Each board's own pages (V5). A page on any other site is the company's own careers site.
ATS_DOMAINS = {"greenhouse": "greenhouse.io", "lever": "lever.co", "ashby": "ashbyhq.com"}

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


async def company_of(session: AsyncSession, app: Application) -> uuid.UUID | None:
    return await session.scalar(select(Listing.company_id).where(Listing.id == app.listing_id))


def on_board(ats: str | None, host: str | None) -> bool:
    """Whether a page from `host` is the board's own, so that a refusal there holds the board
    rather than the company (PRD 1.39). An unknown site or board counts as the board."""
    domain = ATS_DOMAINS.get(ats or "")
    if not host or domain is None:
        return True
    host = host.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


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


async def company_hold_until(
    session: AsyncSession, company_id: uuid.UUID | None, now: datetime
) -> datetime | None:
    """When the hold on this company ends (PRD 1.39), or None when it is not on hold."""
    if company_id is None:
        return None
    hold = await session.get(CompanyHold, company_id)
    if hold is None or hold.until <= now:
        return None
    return hold.until


async def held_companies(session: AsyncSession, now: datetime) -> dict[str, str]:
    """The companies on hold now, by id, with their names (PRD 1.39)."""
    rows = await session.execute(
        select(CompanyHold.company_id, Company.name)
        .join(Company, Company.id == CompanyHold.company_id)
        .where(CompanyHold.until > now)
    )
    return {str(company_id): name for company_id, name in rows.all()}


async def held_until(session: AsyncSession, app: Application, now: datetime) -> datetime | None:
    """When this job may be read or sent again: the later of the holds on its board and on
    its company, or None when neither is on hold."""
    ends = [
        await hold_until(session, await ats_of(session, app), now),
        await company_hold_until(session, await company_of(session, app), now),
    ]
    return max((t for t in ends if t is not None), default=None)


def _strike(hold: AtsHold | CompanyHold, now: datetime, reason: str) -> bool:
    """Start a hold, longer for each strike in a row. False when it was still on: a second
    job hit it before it ended, and the hold stays as it is."""
    if hold.until > now:
        return False
    hold.strikes = int(hold.strikes or 0) + 1
    hold.until = now + HOLDS[min(hold.strikes, len(HOLDS)) - 1]
    hold.reason = reason[:300]
    hold.updated_at = now
    return True


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
    if _strike(hold, now, reason):
        event(log, "apply.board_hold", f"{ats} is on hold until {hold.until.isoformat()}: {reason}",
              level=logging.WARNING, keep=True, ats=ats, strikes=hold.strikes)  # fmt: skip
    return hold.until


async def record_company_block(
    session: AsyncSession, company_id: uuid.UUID, host: str | None, reason: str
) -> datetime:
    """Put one company on hold after its own careers site answered 429 or 403 (PRD 1.39) and
    return when the hold ends. The board and its other companies stay free. The caller
    commits."""
    now = datetime.now(UTC)
    hold = await session.get(CompanyHold, company_id, with_for_update=True)
    if hold is None:
        hold = CompanyHold(company_id=company_id, until=now, strikes=0)
        session.add(hold)
    if _strike(hold, now, reason):
        hold.host = host
        event(log, "apply.company_hold",
              f"{host or 'The company site'} is on hold until {hold.until.isoformat()}: {reason}",
              level=logging.WARNING, keep=True, company_id=str(company_id), host=host,
              strikes=hold.strikes)  # fmt: skip
    return hold.until


async def record_refusal(
    session: AsyncSession, app: Application, host: str | None, reason: str
) -> datetime:
    """This job's form page refused JAP (V5): hold whoever refused, and return when the hold
    ends. `host` is the site of the page that answered, else the job's apply link is used;
    the board's own site holds the board, any other site the company (PRD 1.39). The caller
    commits."""
    ats = await ats_of(session, app)
    host = host or urlsplit(str((app.jd_snapshot or {}).get("apply_url") or "")).hostname
    company = await company_of(session, app)
    if company is not None and not on_board(ats, host):
        return await record_company_block(session, company, host, reason)
    return await record_block(session, ats, reason)


async def clear_strikes(
    session: AsyncSession, ats: str | None, company_id: uuid.UUID | None = None
) -> None:
    """A confirmed submit on this ATS, at this company: the next block of either starts
    again at 1 hour."""
    holds: list[AtsHold | CompanyHold | None] = [
        await session.get(AtsHold, ats) if ats else None,
        await session.get(CompanyHold, company_id) if company_id else None,
    ]
    for hold in holds:
        if hold is not None and hold.strikes:
            hold.strikes = 0


# When Submit last went out on an attempt: the confirmation, or the press when none came.
# A pressed submit with no confirmation may well have reached the board, so it counts for
# the gaps too (JAP-13: Spark went 26 seconds after an unconfirmed Turso).
SENT_AT = func.coalesce(Application.submitted_at, Application.submit_started_at)


@dataclass
class CompanyWindow:
    """A company's own cap (JAP-13) as it stands now: what was sent inside the window, and
    when it has room again (None when it has room now)."""

    company_id: uuid.UUID
    name: str
    limit: int
    days: int
    sent: list[tuple[datetime, uuid.UUID]]  # (when Submit went out, attempt), oldest first
    opens_at: datetime | None


async def company_window(
    session: AsyncSession, company: Company, now: datetime, *, but: uuid.UUID | None = None
) -> CompanyWindow | None:
    """The window of a company with a known cap, not counting attempt `but` itself."""
    if not company.apply_limit or not company.apply_limit_days:
        return None
    days = timedelta(days=company.apply_limit_days)
    q = (
        select(SENT_AT, Application.id)
        .join(Listing, Listing.id == Application.listing_id)
        .where(Listing.company_id == company.id, SENT_AT >= now - days)
        .order_by(SENT_AT)
    )
    if but is not None:
        q = q.where(Application.id != but)
    sent = [(at, app_id) for at, app_id in (await session.execute(q)).all()]
    opens_at = None
    if len(sent) >= company.apply_limit:
        # room again once all but limit - 1 of them have left the window
        opens_at = sent[len(sent) - company.apply_limit][0] + days
    return CompanyWindow(
        company.id, company.name, company.apply_limit, company.apply_limit_days, sent, opens_at
    )


def local_date(at: datetime) -> str:
    """'18 Jan 2027, 6:02 PM' in JAP_TZ."""
    local = at.astimezone(ZoneInfo(get_settings().jap_tz))
    return f"{local:%d %b %Y}, {local:%I:%M %p}".replace(", 0", ", ")


async def company_limit(
    session: AsyncSession, company_id: uuid.UUID, now: datetime, *, but: uuid.UUID | None = None
) -> str | None:
    """Why this company takes no more from me now, by its own stated cap (JAP-13), or None."""
    company = await session.get(Company, company_id)
    window = company and await company_window(session, company, now, but=but)
    if not window or window.opens_at is None:
        return None
    return (
        f"Company limit: {window.name} takes up to {window.limit} applications in "
        f"{window.days} days, and JAP has sent {len(window.sent)}. Nothing was sent; it can "
        f"try again from {local_date(window.opens_at)}"
    )


async def companies_at_limit(session: AsyncSession, now: datetime) -> set[str]:
    """Company ids whose own cap is used up (JAP-13), for pick to pass over."""
    rows = await session.scalars(select(Company.id).where(Company.apply_limit.is_not(None)))
    return {str(c) for c in rows if await company_limit(session, c, now)}


async def submitted_on(session: AsyncSession, ats: str, since: datetime) -> int:
    n = await session.scalar(
        select(func.count())
        .select_from(Application)
        .join(Posting, Posting.id == Application.posting_id)
        .where(Posting.source == ats, Application.submitted_at >= since)
    )
    return int(n or 0)


async def ats_wait(
    session: AsyncSession, ats: str | None, now: datetime, *, but: uuid.UUID | None = None
) -> datetime | None:
    """V4 and V5 for one submit (not counting attempt `but` itself): when it may go on this
    ATS, or None to go now."""
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
        select(func.max(SENT_AT))
        .join(Posting, Posting.id == Application.posting_id)
        .where(Posting.source == ats, true() if but is None else Application.id != but)
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
