"""Stopping following a company (PRD I10 to I15): its jobs close, nothing is deleted.

Every open posting of the board is marked Closed with the reason unfollowed, and each job
(listing) left with no open posting closes with it. A job merged from two boards stays open
while another followed board still has it. A job with an application is never closed or
reset: refresh_listings only closes Not applied listings, so Sent, Failed and in-progress
ones keep their status, and hidden ones stay hidden.

Following again only switches the board back on (api/boards.py). The next search reopens
what the board still lists, on the same rows (pipeline/ingest.py).

`python -m pipeline.unfollow` closes what boards unfollowed before this rule left open. It
only reports unless given --apply.
"""

import asyncio
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Listing, Posting, WatchedBoard
from database.session import get_sessionmaker
from pipeline.dedup import refresh_listings

IN_HISTORY = ("ready_for_review", "applying", "applied", "needs_input", "failed", "manual_apply")


@dataclass
class Effect:
    """What unfollowing a board does, in jobs (listings) as the Jobs page counts them."""

    open_postings: int = 0
    hidden: int = 0  # Not applied jobs that leave the Jobs page
    kept: int = 0  # jobs with an application: they stay in my history
    unread: int = 0  # postings whose AI reading is no longer done


def _other_open(board_id: uuid.UUID):  # type: ignore[no-untyped-def]
    """This listing has an open posting on another board I follow."""
    other = Posting.__table__.alias("other")
    return (
        select(other.c.id)
        .join(WatchedBoard, WatchedBoard.id == other.c.watched_board_id)
        .where(
            other.c.listing_id == Listing.id,
            other.c.watched_board_id != board_id,
            other.c.state == "open",
            WatchedBoard.active,
        )
        .exists()
    )


async def effect(session: AsyncSession, board_id: uuid.UUID) -> Effect:
    """What unfollowing would do, without doing it (the confirmation, I15)."""
    mine = [Posting.watched_board_id == board_id, Posting.state == "open"]
    out = Effect()
    out.open_postings = int(
        await session.scalar(select(func.count()).select_from(Posting).where(*mine)) or 0
    )
    out.unread = int(
        await session.scalar(
            select(func.count())
            .select_from(Posting)
            .where(*mine, Posting.extracted_hash.is_distinct_from(Posting.description_hash))
        )
        or 0
    )
    on_board = select(Posting.listing_id).where(*mine, Posting.listing_id.is_not(None))
    listings = select(func.count()).select_from(Listing).where(Listing.id.in_(on_board))
    out.hidden = int(
        await session.scalar(
            listings.where(
                Listing.status == "not_applied",
                Listing.matched.is_not(None),
                ~_other_open(board_id),
            )
        )
        or 0
    )
    out.kept = int(await session.scalar(listings.where(Listing.status.in_(IN_HISTORY))) or 0)
    return out


async def close_board(session: AsyncSession, board_id: uuid.UUID) -> int:
    """Close the board's open postings as unfollowed and refresh their listings. The caller
    holds the board row locked and commits. Returns how many postings were closed."""
    rows = (
        await session.execute(
            update(Posting)
            .where(Posting.watched_board_id == board_id, Posting.state == "open")
            .values(state="closed", closed_at=func.now(), close_reason="unfollowed")
            .returning(Posting.listing_id)
        )
    ).all()
    await refresh_listings(session, {lid for (lid,) in rows if lid is not None})
    return len(rows)


async def unfollow(session: AsyncSession, board_id: uuid.UUID) -> Effect | None:
    """Stop following the board and close its jobs, in one transaction. None: no such board.

    The board row is locked first, so a search storing this board's jobs right now either
    finishes before (and what it stored is closed here) or finds the board off (I14)."""
    board = await session.get(WatchedBoard, board_id, with_for_update=True)
    if board is None:
        return None
    out = await effect(session, board_id)
    board.active = False
    await close_board(session, board_id)
    await session.commit()
    return out


async def leftovers(session: AsyncSession) -> list[tuple[WatchedBoard, int]]:
    """Unfollowed boards that still have open postings, with how many."""
    rows = await session.execute(
        select(WatchedBoard, func.count(Posting.id))
        .join(Posting, Posting.watched_board_id == WatchedBoard.id)
        .where(WatchedBoard.active.is_(False), Posting.state == "open")
        .group_by(WatchedBoard.id)
        .order_by(WatchedBoard.display_name)
    )
    return [(board, int(n)) for board, n in rows.all()]


async def close_leftovers(apply: bool) -> str:
    lines: list[str] = []
    async with get_sessionmaker()() as session:
        found = await leftovers(session)
        total = 0
        for board, n in found:
            total += n
            lines.append(f"  {board.display_name} ({board.ats}): {n} open postings")
            if apply:
                await session.get(WatchedBoard, board.id, with_for_update=True)
                await close_board(session, board.id)
                await session.commit()
    if not found:
        return "No unfollowed company has open postings. Nothing to do."
    verb = "Closed" if apply else "Would close"
    lines.append(f"{verb} {total} postings of {len(found)} unfollowed companies.")
    if not apply:
        lines.append("Nothing was changed. Run again with APPLY=1 to close them.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(asyncio.run(close_leftovers("--apply" in sys.argv[1:])))
