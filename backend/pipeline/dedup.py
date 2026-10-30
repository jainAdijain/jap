"""Cross-platform dedup (D1 to D6): every posting joins exactly one listing.

A posting with no listing yet is placed, in this order: my remembered merge or split
(dedup_override), a shared requisition ID, an exact match on (canonical company, canonical
role), a near match (title similarity 0.9 or more and description cosine 0.92 or more), or a
new listing. A pair just below the near-match bar becomes a Possible duplicate, which
settle_duplicates() hands to the extract model: it reads both descriptions once and JAP
merges the pair or keeps it apart (PRD 1.7, Near match). I am never asked. Placed postings
keep their listing on later runs, so merges and splits stay put and a repost of an old role
joins its old listing (D4).
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Company,
    CompanyAlias,
    DedupOverride,
    Listing,
    PossibleDuplicate,
    Posting,
    WatchedBoard,
)
from llm import PersonalDataBlocked, Sensitive, SpendCapReached, call
from llm.client import ModelCallError
from pipeline.canonical import canonical_role, clean_company, seniority_of

log = logging.getLogger(__name__)

NEAR_TITLE, NEAR_COSINE = 0.9, 0.92  # PRD Near match
MAYBE_TITLE, MAYBE_COSINE = 0.75, 0.85  # just below: the model decides (settle_duplicates)
MAX_JD_CHARS = 6_000  # per side; enough to tell two jobs apart, and it caps the cost
WORK_MODES = {"remote", "hybrid", "onsite"}
# D1: a supported ATS first (all three in v1), then careers pages, then anything else.
SOURCE_PRIORITY = {"greenhouse": 0, "lever": 0, "ashby": 0}


class DedupConflict(Exception):
    """A merge or split that cannot be done (shown to me as a 409)."""


@dataclass
class DedupCounts:
    placed: int = 0
    merged: int = 0  # joined a listing that already existed (D6)
    new_listings: int = 0
    possible_duplicates: int = 0
    how: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def as_dict(self) -> dict[str, Any]:
        return {
            "merged": self.merged,
            "new_listings": self.new_listings,
            "possible_duplicates": self.possible_duplicates,
        }


async def resolve_company(session: AsyncSession, name: str) -> Company:
    key = clean_company(name) or name.lower().strip()
    alias = await session.get(CompanyAlias, key)
    if alias is not None:
        company = await session.get(Company, alias.company_id)
        if company is not None:
            return company
    await session.execute(
        insert(Company)
        .values(name=name, canonical_name=key)
        .on_conflict_do_nothing(index_elements=["canonical_name"])
    )
    company = await session.scalar(select(Company).where(Company.canonical_name == key))
    assert company is not None
    return company


async def _cosine_candidates(
    session: AsyncSession, posting_id: uuid.UUID, company_id: uuid.UUID, role: str
) -> list[tuple[uuid.UUID, str, float, float]]:
    rows = await session.execute(
        text(
            """
            SELECT l.id, l.canonical_role, similarity(l.canonical_role, :role) AS title_sim,
                   1 - (pp.embedding <=> p.embedding) AS cosine
            FROM listing l
            JOIN posting pp ON pp.id = l.primary_posting_id
            JOIN posting p ON p.id = :pid
            WHERE l.company_id = :cid AND pp.embedding IS NOT NULL AND p.embedding IS NOT NULL
              AND pp.id <> p.id AND similarity(l.canonical_role, :role) >= :floor
            ORDER BY title_sim DESC, cosine DESC
            """
        ),
        {"role": role, "pid": posting_id, "cid": company_id, "floor": MAYBE_TITLE},
    )
    return [(r[0], r[1], float(r[2]), float(r[3])) for r in rows.all()]


async def _place(
    session: AsyncSession, p: Posting, company: Company, role: str, counts: DedupCounts
) -> tuple[uuid.UUID, bool]:
    """The listing this posting joins, and whether that listing was just created for it."""
    override = await session.scalar(
        select(DedupOverride).where(
            DedupOverride.posting_source == p.source,
            DedupOverride.posting_external_id == p.external_id,
        )
    )
    if override is not None and override.listing_id is not None:
        if await session.get(Listing, override.listing_id) is not None:
            counts.how["override"] += 1
            return override.listing_id, False

    if p.requisition_id:
        shared = await session.execute(
            select(Listing.id, Listing.canonical_role)
            .join(Posting, Posting.listing_id == Listing.id)
            .where(
                Listing.company_id == company.id,
                Posting.requisition_id == p.requisition_id,
                Posting.id != p.id,
            )
        )
        for listing_id, other_role in shared:
            # A reused requisition ID never joins Senior to Junior (seniority stays in the role).
            if seniority_of(other_role) == seniority_of(role):
                counts.how["requisition"] += 1
                return listing_id, False

    exact = await session.scalar(
        select(Listing.id).where(Listing.company_id == company.id, Listing.canonical_role == role)
    )
    if exact is not None:
        counts.how["exact"] += 1
        return exact, False

    maybe: tuple[uuid.UUID, float, float] | None = None
    for listing_id, other_role, title_sim, cosine in await _cosine_candidates(
        session, p.id, company.id, role
    ):
        if seniority_of(other_role) != seniority_of(role):
            continue  # Senior and Junior are never the same role
        if title_sim >= NEAR_TITLE and cosine >= NEAR_COSINE:
            counts.how["near"] += 1
            return listing_id, False
        if maybe is None and cosine >= MAYBE_COSINE:
            maybe = (listing_id, title_sim, cosine)

    listing = Listing(
        company_id=company.id,
        canonical_role=role,
        title_display=p.title_raw,
        primary_posting_id=p.id,
    )
    session.add(listing)
    await session.flush()
    counts.new_listings += 1
    if maybe is not None:
        a, b = sorted([listing.id, maybe[0]])
        result = await session.execute(
            insert(PossibleDuplicate)
            .values(
                kind="listing",
                a_id=a,
                b_id=b,
                scores={"title": round(maybe[1], 3), "cosine": round(maybe[2], 3)},
            )
            .on_conflict_do_nothing(index_elements=["kind", "a_id", "b_id"])
            .returning(PossibleDuplicate.id)
        )
        counts.possible_duplicates += len(result.all())
    return listing.id, True


async def dedup(session: AsyncSession, since: datetime | None = None) -> DedupCounts:
    """Place every posting that has no listing, then refresh the listings that changed.

    since: also refresh listings whose postings were fetched from then on (closed ones too).
    """
    counts = DedupCounts()
    touched: set[uuid.UUID] = set()
    postings = list(
        await session.scalars(
            select(Posting)
            .where(Posting.listing_id.is_(None))
            .order_by(Posting.posted_at.asc().nulls_last(), Posting.first_seen_at)
        )
    )
    boards: dict[uuid.UUID, WatchedBoard | None] = {}
    for p in postings:
        company = await resolve_company(session, p.company_name_raw)
        if p.watched_board_id not in boards:
            boards[p.watched_board_id] = await session.get(WatchedBoard, p.watched_board_id)
        board = boards[p.watched_board_id]
        if board is not None and board.company_id is None:
            board.company_id = company.id  # I9: the board links to its canonical company
        role = canonical_role(p.title_raw, p.locations) or p.title_raw.lower()
        p.canonical_role = role
        listing_id, created = await _place(session, p, company, role, counts)
        if not created:
            counts.merged += 1
        p.listing_id = listing_id
        touched.add(listing_id)
        counts.placed += 1
        await session.flush()
    if since is not None:
        changed = await session.scalars(
            select(Posting.listing_id)
            .where(Posting.updated_at >= since, Posting.listing_id.is_not(None))
            .distinct()
        )
        touched.update(lid for lid in changed if lid is not None)
    await refresh_listings(session, touched)
    await session.commit()
    return counts


def _when(p: Posting) -> datetime:
    return p.posted_at or p.first_seen_at


async def refresh_listings(session: AsyncSession, listing_ids: set[uuid.UUID]) -> None:
    """Recompute each listing from its postings: primary (D1, D2), merged locations (D3),
    extracted fields, posted dates and Closed. A listing left with no postings is removed."""
    if not listing_ids:
        return
    by_listing: dict[uuid.UUID, list[Posting]] = defaultdict(list)
    for p in await session.scalars(select(Posting).where(Posting.listing_id.in_(listing_ids))):
        assert p.listing_id is not None
        by_listing[p.listing_id].append(p)
    for listing_id in listing_ids:
        listing = await session.get(Listing, listing_id)
        if listing is None:
            continue
        postings = by_listing.get(listing_id, [])
        if not postings:
            await _drop_listing(session, listing)
            continue
        open_ = [p for p in postings if p.state == "open"]
        pool = open_ or postings
        primary = min(pool, key=lambda p: (SOURCE_PRIORITY.get(p.source, 9), -_when(p).timestamp()))
        listing.primary_posting_id = primary.id
        listing.title_display = primary.title_raw
        locations: list[str] = []
        skills: list[str] = []
        regions: list[str] = []
        for p in sorted(pool, key=lambda p: p.id != primary.id):
            locations += [loc for loc in p.locations if loc not in locations]
            extracted = p.extracted or {}
            skills += [s for s in extracted.get("skills") or [] if s not in skills]
            regions += [r for r in extracted.get("remote_regions") or [] if r not in regions]
        listing.locations = locations
        listing.skills = skills
        listing.remote_regions = regions
        fields = primary.extracted or {}
        mode = fields.get("work_mode")
        listing.work_mode = mode if mode in WORK_MODES else None
        seniority = fields.get("seniority")
        listing.seniority = seniority if seniority and seniority != "unknown" else None
        listing.first_posted_at = min(_when(p) for p in postings)
        listing.last_posted_at = max(_when(p) for p in postings)
        if not open_ and listing.status == "not_applied":
            listing.status = "closed"
            closed = [p.closed_at for p in postings if p.closed_at]
            listing.closed_at = max(closed) if closed else func.now()
        elif open_ and listing.status == "closed":
            listing.status = "not_applied"
            listing.closed_at = None
    await session.flush()


async def _drop_listing(session: AsyncSession, listing: Listing) -> None:
    has_attempts = await session.scalar(
        text("SELECT EXISTS (SELECT 1 FROM application WHERE listing_id = :id)"),
        {"id": listing.id},
    )
    if has_attempts:
        return  # an applied listing is never removed (D4)
    await session.execute(
        delete(PossibleDuplicate).where(
            or_(PossibleDuplicate.a_id == listing.id, PossibleDuplicate.b_id == listing.id)
        )
    )
    await session.execute(
        update(DedupOverride).where(DedupOverride.listing_id == listing.id).values(listing_id=None)
    )
    await session.delete(listing)


async def _remember(session: AsyncSession, p: Posting, kind: str, listing_id: uuid.UUID) -> None:
    stmt = insert(DedupOverride).values(
        kind=kind, posting_source=p.source, posting_external_id=p.external_id, listing_id=listing_id
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["posting_source", "posting_external_id"],
            set_={"kind": kind, "listing_id": listing_id},
        )
    )


async def merge_listings(session: AsyncSession, keep_id: uuid.UUID, other_id: uuid.UUID) -> None:
    """Move every posting of other into keep, remembered per posting (D5)."""
    if keep_id == other_id:
        raise DedupConflict("Pick a different listing to merge with")
    keep = await session.get(Listing, keep_id)
    other = await session.get(Listing, other_id)
    if keep is None or other is None:
        raise LookupError("No such listing")
    if other.status not in ("not_applied", "closed", "skipped"):
        raise DedupConflict("That listing has an application; merge the other way round")
    moved = list(await session.scalars(select(Posting).where(Posting.listing_id == other_id)))
    for p in moved:
        p.listing_id = keep_id
        await _remember(session, p, "merge", keep_id)
    pair = sorted([keep_id, other_id])
    await session.execute(
        update(PossibleDuplicate)
        .where(PossibleDuplicate.a_id == pair[0], PossibleDuplicate.b_id == pair[1])
        .values(decision="merged")
    )
    await session.flush()
    await refresh_listings(session, {keep_id, other_id})
    await session.commit()


async def split_posting(session: AsyncSession, posting_id: uuid.UUID) -> uuid.UUID:
    """Give one posting a listing of its own, remembered (D5). Returns the new listing."""
    p = await session.get(Posting, posting_id)
    if p is None or p.listing_id is None:
        raise LookupError("No such job")
    old = await session.get(Listing, p.listing_id)
    assert old is not None
    others = await session.scalar(
        select(func.count()).where(Posting.listing_id == old.id, Posting.id != p.id)
    )
    if not others:
        raise DedupConflict("This job is already a listing of its own")
    # The role key must stay unique per company; new postings of this role keep joining
    # the old listing, which is the one with the plain key.
    listing = Listing(
        company_id=old.company_id,
        canonical_role=f"{p.canonical_role or old.canonical_role} (split {str(p.id)[:8]})",
        title_display=p.title_raw,
        primary_posting_id=p.id,
    )
    session.add(listing)
    await session.flush()
    p.listing_id = listing.id
    await _remember(session, p, "split", listing.id)
    await session.flush()
    await refresh_listings(session, {old.id, listing.id})
    await session.commit()
    return listing.id


async def decide_duplicate(session: AsyncSession, dup_id: uuid.UUID, decision: str) -> None:
    dup = await session.get(PossibleDuplicate, dup_id)
    if dup is None:
        raise LookupError("No such pair")
    if dup.decision != "open":
        raise DedupConflict("This pair is already decided")
    if decision == "merged":
        keep, other = await session.get(Listing, dup.a_id), await session.get(Listing, dup.b_id)
        if keep is None or other is None:
            raise LookupError("One of the listings is gone")
        if (other.first_posted_at or other.created_at) < (keep.first_posted_at or keep.created_at):
            keep, other = other, keep  # keep the older listing
        await merge_listings(session, keep.id, other.id)
    else:
        dup.decision = "kept_apart"
        await session.commit()


class SameJob(BaseModel):
    same: bool
    reason: str


@dataclass
class Settled:
    merged: int = 0
    kept_apart: int = 0
    undecided: int = 0  # the model could not answer; tried again on the next run


async def settle_duplicates(session: AsyncSession) -> Settled:
    """Let the extract model decide every open Possible duplicate (PRD 1.7, Near match).

    The pair's primary postings go to the model with their titles, locations and
    descriptions; same merges into the older listing (remembered per posting), not same
    keeps them apart with the model's reason in scores. A pair the model can't answer (down,
    the AI limit, a broken reply) stays open and is two jobs until the next run tries again.
    """
    out = Settled()
    pairs = list(
        await session.scalars(
            select(PossibleDuplicate).where(
                PossibleDuplicate.decision == "open", PossibleDuplicate.kind == "listing"
            )
        )
    )
    for dup in pairs:
        a, b = await session.get(Listing, dup.a_id), await session.get(Listing, dup.b_id)
        pa = await session.get(Posting, a.primary_posting_id) if a else None
        pb = await session.get(Posting, b.primary_posting_id) if b else None
        if pa is None or pb is None:
            continue
        try:
            result = await call(
                session,
                "same_job",
                {
                    "title_a": pa.title_raw,
                    "locations_a": "; ".join(pa.locations) or "not stated",
                    "description_a": pa.description_text[:MAX_JD_CHARS],
                    "title_b": pb.title_raw,
                    "locations_b": "; ".join(pb.locations) or "not stated",
                    "description_b": pb.description_text[:MAX_JD_CHARS],
                },
                SameJob,
                sensitive=Sensitive(),
                generic_redact=frozenset({"description_a", "description_b"}),
            )
        except (ModelCallError, SpendCapReached, PersonalDataBlocked) as exc:
            log.warning("possible duplicate %s left open: %s", dup.id, exc)
            out.undecided += 1
            continue
        dup.scores = {**dup.scores, "same": result.output.same, "reason": result.output.reason}
        await session.flush()
        try:
            decision = "merged" if result.output.same else "kept_apart"
            await decide_duplicate(session, dup.id, decision)
        except DedupConflict as exc:  # both have applications: never merged away
            log.warning("possible duplicate %s kept apart: %s", dup.id, exc)
            dup.decision = "kept_apart"
            await session.commit()
            out.kept_apart += 1
            continue
        if result.output.same:
            out.merged += 1
        else:
            out.kept_apart += 1
    return out
