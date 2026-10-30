"""Listings (U1 to U4, U11 to U13, D1 to D5, F1 to F3): one row per company and role.

Only listings that match My profile are shown (listing.matched, written by
pipeline.matching), newest first. A listing is hidden when none of its postings comes from a
company I still watch. The filter bar narrows by role, location, status and matched
condition without touching the profile (F2).
"""

import uuid
from collections import defaultdict

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, Text, cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from api.applications import to_out as application_out
from api.errors import ApiError
from api.postings import detail
from database.models import Application, Company, Listing, Posting, Profile, WatchedBoard
from database.session import Session
from functions.events import send
from pipeline.apply.flow import ACTIVE, MAYBE_SENT, maybe_sent, why_not
from pipeline.dedup import DedupConflict, merge_listings
from pipeline.matching import refilter_from_profile
from schemas.application import ApplyQueued, RetryIn
from schemas.job import ATS
from schemas.listing import (
    AlsoOn,
    ListingDetail,
    ListingPage,
    ListingRef,
    ListingRow,
    ListingSummary,
    MergeIn,
    RoleCounts,
    StatusCounts,
)

STATUSES = (
    "not_applied", "ready_for_review", "applying", "applied", "needs_input", "failed",
    "manual_apply", "skipped", "closed",
)  # fmt: skip
STATUS_FILTER = "^(open|all|" + "|".join(STATUSES) + ")$"
OTHER_ROLE = "__other"  # role filter value for listings matched through Skills or Location

router = APIRouter(prefix="/listings", tags=["listings"])


def watched() -> ColumnElement[bool]:
    return exists().where(
        Posting.listing_id == Listing.id,
        Posting.watched_board_id.in_(select(WatchedBoard.id).where(WatchedBoard.active)),
    )


async def postings_of(
    session: AsyncSession, ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[Posting]]:
    out: dict[uuid.UUID, list[Posting]] = defaultdict(list)
    if ids:
        for p in await session.scalars(select(Posting).where(Posting.listing_id.in_(ids))):
            assert p.listing_id is not None
            out[p.listing_id].append(p)
    return out


def platforms(postings: list[Posting]) -> list[ATS]:
    found = {p.source for p in postings if p.state == "open"} or {p.source for p in postings}
    return [ATS(s) for s in sorted(found)]


async def nice_to_have(session: AsyncSession) -> list[str]:
    profile = await session.scalar(select(Profile))
    return list(profile.nice_to_have_skills) if profile else []


def to_row(listing: Listing, company: str, postings: list[Posting], nice: list[str]) -> ListingRow:
    return ListingRow(
        id=listing.id,
        title=listing.title_display,
        company=company,
        locations=listing.locations,
        work_mode=listing.work_mode,
        status=listing.status,
        last_posted_at=listing.last_posted_at,
        platforms=platforms(postings),
        postings=len(postings),
        role=listing.canonical_role,
        matched=listing.matched or [],
        matched_role=listing.matched_role,
        nice_skills=[s for s in nice if s in listing.skills],
    )


def listed() -> list[ColumnElement[bool]]:
    return [Listing.matched.is_not(None), watched()]


@router.get("", response_model=ListingPage)
async def listings(
    session: Session,
    status: str = Query("open", pattern=STATUS_FILTER),
    role: str | None = Query(None, max_length=200),
    location: str | None = Query(None, max_length=100),
    matched: str | None = Query(None, pattern="^(role|skills|location)$"),
    q: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> ListingPage:
    where = listed()
    if status == "open":
        where.append(Listing.status != "closed")
    elif status != "all":
        where.append(Listing.status == status)
    if role == OTHER_ROLE:
        where.append(Listing.matched_role.is_(None))
    elif role:
        where.append(Listing.matched_role == role)
    if location:
        where.append(func.array_to_string(Listing.locations, " | ").ilike(f"%{location.strip()}%"))
    if matched:
        where.append(Listing.matched.contains(cast([matched], ARRAY(Text))))
    if q:
        like = f"%{q}%"
        where.append(or_(Listing.title_display.ilike(like), Company.name.ilike(like)))
    base = select(Listing, Company.name).join(Company, Company.id == Listing.company_id)
    total = await session.scalar(
        select(func.count())
        .select_from(Listing)
        .join(Company, Company.id == Listing.company_id)
        .where(*where)
    )
    rows = (
        await session.execute(
            base.where(*where)
            .order_by(Listing.last_posted_at.desc().nulls_last(), Listing.title_display)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    by_listing = await postings_of(session, [listing.id for listing, _ in rows])
    nice = await nice_to_have(session)
    return ListingPage(
        total=int(total or 0),
        page=page,
        page_size=page_size,
        rows=[to_row(listing, name, by_listing[listing.id], nice) for listing, name in rows],
    )


@router.get("/summary", response_model=ListingSummary)
async def summary(session: Session) -> ListingSummary:
    """Totals and Jobs by role over the listed, not Closed listings (U1, U2). Each listing
    counts once, under the first target role it matches; the rest count under Other."""
    rows = await session.execute(
        select(Listing.matched_role, Listing.status, func.count())
        .where(*listed(), Listing.status != "closed")
        .group_by(Listing.matched_role, Listing.status)
    )
    profile = await session.scalar(select(Profile))
    roles: list[str | None] = [*(profile.target_roles if profile else []), None]
    totals = StatusCounts()
    by_role: dict[str | None, RoleCounts] = {r: RoleCounts(role=r) for r in roles}
    for role, status, n in rows.all():
        counts = by_role.setdefault(role, RoleCounts(role=role))
        for c in (totals, counts):
            c.listings += n
            setattr(c, status, getattr(c, status) + n)
    return ListingSummary(totals=totals, by_role=list(by_role.values()))


async def load_detail(session: AsyncSession, listing_id: uuid.UUID) -> ListingDetail:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise ApiError(404, "not_found", "No such listing")
    company = await session.get(Company, listing.company_id)
    assert company is not None
    postings = (await postings_of(session, [listing.id]))[listing.id]
    primary = next((p for p in postings if p.id == listing.primary_posting_id), None)
    others = sorted(
        (p for p in postings if p.id != listing.primary_posting_id),
        key=lambda p: (p.state != "open", p.source, p.title_raw),
    )
    attempts = list(
        await session.scalars(
            select(Application)
            .where(Application.listing_id == listing.id)
            .order_by(Application.created_at.desc())
        )
    )
    same = await session.execute(
        select(Listing.id, Listing.title_display)
        .where(Listing.company_id == listing.company_id, Listing.id != listing.id, watched())
        .order_by(Listing.title_display)
    )
    return ListingDetail(
        **to_row(listing, company.name, postings, await nice_to_have(session)).model_dump(),
        skills=listing.skills,
        seniority=listing.seniority,
        remote_regions=listing.remote_regions,
        primary=detail(primary) if primary else None,
        also_on=[
            AlsoOn(
                posting_id=p.id,
                source=ATS(p.source),
                title=p.title_raw,
                company=p.company_name_raw,
                locations=p.locations,
                posting_url=p.posting_url,
                state=p.state,
                posted_at=p.posted_at,
            )
            for p in others
        ],
        same_company=[ListingRef(id=i, title=t) for i, t in same.all()],
        application=await application_out(session, attempts[0]) if attempts else None,
        attempts=len(attempts),
        apply_blocked=await why_not(session, listing.id),
    )


@router.get("/{listing_id}", response_model=ListingDetail)
async def listing(listing_id: uuid.UUID, session: Session) -> ListingDetail:
    return await load_detail(session, listing_id)


@router.post("/{listing_id}/merge", response_model=ListingDetail)
async def merge(listing_id: uuid.UUID, body: MergeIn, session: Session) -> ListingDetail:
    """Merge the other listing into this one (D5); remembered on later runs."""
    try:
        await merge_listings(session, listing_id, body.other_listing_id)
        await refilter_from_profile(session, [listing_id])
        await session.commit()
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    except DedupConflict as exc:
        raise ApiError(409, "conflict", str(exc)) from exc
    return await load_detail(session, listing_id)


@router.post("/{listing_id}/apply", response_model=ApplyQueued, status_code=202)
async def apply_now(
    listing_id: uuid.UUID, session: Session, body: RetryIn | None = None
) -> ApplyQueued:
    """Apply now (one listing): prepares it; in Review mode it stops at Ready for review."""
    reason = await why_not(session, listing_id)
    if reason:
        raise ApiError(409, "conflict", reason)
    if await maybe_sent(session, listing_id) and not (body and body.checked):
        raise ApiError(409, "submit_maybe_sent", MAYBE_SENT)
    await send("listing/apply.requested", {"listing_id": str(listing_id), "run_id": None})
    return ApplyQueued(listing_id=listing_id, queued=True)


@router.post("/{listing_id}/skip", response_model=ListingDetail)
async def skip(listing_id: uuid.UUID, session: Session) -> ListingDetail:
    """Skip: I chose not to apply. An attempt waiting for me is cancelled."""
    listing = await session.get(Listing, listing_id, with_for_update=True)
    if listing is None:
        raise ApiError(404, "not_found", "No such listing")
    if listing.status == "applied":
        raise ApiError(409, "conflict", "This job is Applied")
    active = list(
        await session.scalars(
            select(Application)
            .where(Application.listing_id == listing_id, Application.status.in_(ACTIVE))
            .with_for_update()
        )
    )
    if any(a.status == "applying" for a in active):
        raise ApiError(409, "conflict", "JAP is filling or submitting this form right now")
    for a in active:
        a.status = "cancelled"
    listing.status = "skipped"
    await session.commit()
    if active:
        await send("application/cancelled", {"listing_id": str(listing_id)})
    return await load_detail(session, listing_id)


@router.post("/{listing_id}/unskip", response_model=ListingDetail)
async def unskip(listing_id: uuid.UUID, session: Session) -> ListingDetail:
    """Undo Skip: back to Not applied (or Closed if every posting closed meanwhile)."""
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise ApiError(404, "not_found", "No such listing")
    if listing.status != "skipped":
        raise ApiError(409, "conflict", "This job is not Skipped")
    is_open = await session.scalar(
        select(Posting.id).where(Posting.listing_id == listing_id, Posting.state == "open").limit(1)
    )
    listing.status = "not_applied" if is_open else "closed"
    await session.commit()
    return await load_detail(session, listing_id)
