"""make demo / make demo-clear: made-up postings for checking dedup by hand (Phase 3).

Loads "JAP Demo Co" with a Greenhouse and a Lever board (origin demo, never fetched by a
run) and six postings: one role posted on both boards, a Senior and a Junior version, a
different specialization, and a borderline pair that should show as Possible duplicate.
Embeddings and extracted fields are written directly, so loading costs nothing and needs
no Ollama. make demo-clear removes all of it.
"""

import asyncio
import hashlib
import math
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text

from database.models import DedupOverride, Posting, WatchedBoard
from database.session import get_sessionmaker
from pipeline.dedup import dedup, refresh_listings
from pipeline.ingest import html_to_text

COMPANY = "JAP Demo Co"
TOKEN = "jap-demo"
DIM = 768


def _vector(angle_from_base: float) -> str:
    """A unit vector whose cosine with the base vector is cos(angle)."""
    v = [0.0] * DIM
    v[0], v[1] = math.cos(angle_from_base), math.sin(angle_from_base)
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


# (board, external id, title, locations, days ago, description, cosine with the base vector)
POSTINGS: list[tuple[str, str, str, list[str], int, str, float]] = [
    ("greenhouse", "demo-1", "Sr. Backend Engineer (Remote)", ["Remote (India)"], 2,
     "Build payment APIs in Python and Postgres.", 1.0),
    ("lever", "demo-2", "Senior Backend Engineer - Bangalore", ["Bangalore, India"], 1,
     "Build payment APIs in Python and Postgres.", 1.0),
    ("greenhouse", "demo-3", "Junior Backend Engineer", ["Bangalore, India"], 3,
     "Build payment APIs in Python and Postgres. Mentored by senior engineers.", 1.0),
    ("greenhouse", "demo-4", "Senior Frontend Engineer", ["Remote (India)"], 4,
     "Build the dashboard in React and TypeScript.", 1.0),
    ("greenhouse", "demo-5", "Senior Data Platform Engineer", ["Pune, India"], 5,
     "Run our Kafka and Spark data platform.", 1.0),
    ("lever", "demo-6", "Senior Data Platform Engineer, Streaming", ["Pune, India"], 5,
     "Run our Kafka streaming platform and its Spark jobs.", 0.9),
]  # fmt: skip


async def load() -> str:
    async with get_sessionmaker()() as session:
        boards: dict[str, WatchedBoard] = {}
        for ats in ("greenhouse", "lever"):
            board = await session.scalar(
                select(WatchedBoard).where(
                    WatchedBoard.ats == ats, WatchedBoard.board_token == TOKEN
                )
            )
            if board is None:
                board = WatchedBoard(
                    ats=ats, board_token=TOKEN, display_name=COMPANY, origin="demo"
                )
                session.add(board)
            board.active = True
            boards[ats] = board
        await session.flush()
        now = datetime.now(UTC)
        for ats, ext, title, locations, days, body, cosine in POSTINGS:
            if await session.scalar(select(Posting.id).where(Posting.external_id == ext)):
                continue
            html = f"<p>{body}</p>"
            description = html_to_text(html)
            digest = hashlib.sha256(description.encode()).hexdigest()
            extracted: dict[str, Any] = {
                "skills": ["python", "postgres"] if "Backend" in title else ["kafka", "spark"],
                "seniority": "junior" if "Junior" in title else "senior",
                "work_mode": "remote" if "Remote" in locations[0] else "onsite",
                "direct": [],
            }
            p = Posting(
                source=ats,
                external_id=ext,
                watched_board_id=boards[ats].id,
                company_name_raw=COMPANY,
                title_raw=title,
                locations=locations,
                posted_at=now - timedelta(days=days),
                posting_url=f"https://example.com/jap-demo/{ext}",
                apply_url=f"https://example.com/jap-demo/{ext}/apply",
                description_html=html,
                description_text=description,
                description_hash=digest,
                raw_payload={"demo": True},
                extracted=extracted,
                extracted_hash=digest,
                embedded_hash=digest,
            )
            session.add(p)
            await session.flush()
            await session.execute(
                text("UPDATE posting SET embedding = CAST(:v AS vector) WHERE id = :id"),
                {"v": _vector(math.acos(min(1.0, cosine))), "id": p.id},
            )
        await session.commit()
        counts = await dedup(session)
    return (
        f"Loaded {COMPANY}: {counts.placed} postings placed, {counts.merged} merged, "
        f"{counts.new_listings} listings, {counts.possible_duplicates} possible duplicate(s)"
    )


async def clear() -> str:
    async with get_sessionmaker()() as session:
        board_ids = list(
            await session.scalars(select(WatchedBoard.id).where(WatchedBoard.origin == "demo"))
        )
        postings = list(
            await session.scalars(select(Posting).where(Posting.watched_board_id.in_(board_ids)))
        )
        listing_ids = {p.listing_id for p in postings if p.listing_id}
        await session.execute(
            delete(DedupOverride).where(
                DedupOverride.posting_external_id.in_([p.external_id for p in postings])
            )
        )
        for p in postings:
            await session.delete(p)
        await session.flush()
        await refresh_listings(session, listing_ids)  # removes the emptied listings
        await session.execute(delete(WatchedBoard).where(WatchedBoard.id.in_(board_ids)))
        await session.execute(
            text(
                "DELETE FROM company c WHERE c.name = :name AND NOT EXISTS "
                "(SELECT 1 FROM listing l WHERE l.company_id = c.id)"
            ),
            {"name": COMPANY},
        )
        await session.commit()
    return f"Removed {COMPANY}: {len(postings)} postings, {len(listing_ids)} listings"


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "load"
    print(asyncio.run(load() if action == "load" else clear()))
