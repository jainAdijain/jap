"""Possible duplicates (Near match): borderline pairs I merge or keep apart."""

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from api.errors import ApiError
from api.listings import platforms, postings_of
from database.models import Company, Listing, PossibleDuplicate
from database.session import Session
from pipeline.dedup import DedupConflict, decide_duplicate
from schemas.listing import DecisionIn, DuplicateOut, DuplicateSide

router = APIRouter(prefix="/duplicates", tags=["duplicates"])


@router.get("", response_model=list[DuplicateOut])
async def duplicates(session: Session) -> list[DuplicateOut]:
    pairs = list(
        await session.scalars(
            select(PossibleDuplicate).where(
                PossibleDuplicate.decision == "open", PossibleDuplicate.kind == "listing"
            )
        )
    )
    ids = [i for d in pairs for i in (d.a_id, d.b_id)]
    found = {
        lst.id: lst for lst in await session.scalars(select(Listing).where(Listing.id.in_(ids)))
    }
    by_listing = await postings_of(session, ids)
    out: list[DuplicateOut] = []
    for d in pairs:
        a, b = found.get(d.a_id), found.get(d.b_id)
        if a is None or b is None:
            continue
        company = await session.get(Company, a.company_id)

        def side(lst: Listing) -> DuplicateSide:
            return DuplicateSide(
                id=lst.id,
                title=lst.title_display,
                platforms=platforms(by_listing[lst.id]),
                locations=lst.locations,
            )

        out.append(
            DuplicateOut(
                id=d.id,
                company=company.name if company else "",
                a=side(a),
                b=side(b),
                title_similarity=float(d.scores.get("title", 0)),
                description_similarity=float(d.scores.get("cosine", 0)),
            )
        )
    return out


@router.post("/{dup_id}", status_code=204)
async def decide(dup_id: uuid.UUID, body: DecisionIn, session: Session) -> None:
    try:
        await decide_duplicate(session, dup_id, body.decision)
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    except DedupConflict as exc:
        raise ApiError(409, "conflict", str(exc)) from exc
