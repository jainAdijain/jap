"""Listings (U1 to U4, U11 to U13, D1 to D5): one row per company and role.

Until the match rule arrives (Phase 4) every listing is shown, newest first. A listing is
hidden when none of its postings comes from a company I still watch.
"""

import uuid
from collections import defaultdict

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from api.postings import detail
from database.models import Company, Listing, Posting, WatchedBoard
from database.session import Session
from pipeline.dedup import DedupConflict, merge_listings
from schemas.job import ATS
from schemas.listing import (
    AlsoOn,
    ListingDetail,
    ListingPage,
    ListingRef,
    ListingRow,
    MergeIn,
)

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


def to_row(listing: Listing, company: str, postings: list[Posting]) -> ListingRow:
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
    )


@router.get("", response_model=ListingPage)
async def listings(
    session: Session,
    status: str = Query("open", pattern="^(open|closed|all)$"),
    q: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> ListingPage:
    where: list[ColumnElement[bool]] = [watched()]
    if status == "open":
        where.append(Listing.status != "closed")
    elif status == "closed":
        where.append(Listing.status == "closed")
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
    return ListingPage(
        total=int(total or 0),
        page=page,
        page_size=page_size,
        rows=[to_row(listing, name, by_listing[listing.id]) for listing, name in rows],
    )


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
    same = await session.execute(
        select(Listing.id, Listing.title_display)
        .where(Listing.company_id == listing.company_id, Listing.id != listing.id, watched())
        .order_by(Listing.title_display)
    )
    return ListingDetail(
        **to_row(listing, company.name, postings).model_dump(),
        canonical_role=listing.canonical_role,
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
    )


@router.get("/{listing_id}", response_model=ListingDetail)
async def listing(listing_id: uuid.UUID, session: Session) -> ListingDetail:
    return await load_detail(session, listing_id)


@router.post("/{listing_id}/merge", response_model=ListingDetail)
async def merge(listing_id: uuid.UUID, body: MergeIn, session: Session) -> ListingDetail:
    """Merge the other listing into this one (D5); remembered on later runs."""
    try:
        await merge_listings(session, listing_id, body.other_listing_id)
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    except DedupConflict as exc:
        raise ApiError(409, "conflict", str(exc)) from exc
    return await load_detail(session, listing_id)
