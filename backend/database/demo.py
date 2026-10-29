"""make demo / make demo-clear: made-up postings for checking dedup and filters by hand.

Loads "JAP Demo Co" with a Greenhouse and a Lever board (origin demo, never fetched by a
run) and six postings: one role posted on both boards, a Senior and a Junior version, a
different specialization, and a borderline pair that should show as Possible duplicate
(Phase 3). It also loads "Globex Demo" on an Ashby board with six jobs for the match rule
(Phase 4): Remote - Worldwide, Remote - US only, Remote - APAC, an Intern title, a job whose
work mode is unclear, and one in Berlin. Embeddings and extracted fields are written
directly, so loading costs nothing and needs no Ollama or model key. make demo-clear
removes all of it.
"""

import asyncio
import hashlib
import math
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DedupOverride, Posting, WatchedBoard
from database.session import get_sessionmaker
from pipeline.dedup import dedup, refresh_listings
from pipeline.ingest import html_to_text
from pipeline.matching import refilter_from_profile

COMPANY = "JAP Demo Co"
TOKEN = "jap-demo"
FILTER_COMPANY = "Globex Demo"
FILTER_TOKEN = "jap-demo-globex"
DIM = 768


def _vector(angle_from_base: float) -> str:
    """A unit vector whose cosine with the base vector is cos(angle)."""
    v = [0.0] * DIM
    v[0], v[1] = math.cos(angle_from_base), math.sin(angle_from_base)
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def _axis(k: int) -> str:
    """A unit vector orthogonal to the base vector and to every other axis."""
    v = [0.0] * DIM
    v[10 + k] = 1.0
    return "[" + ",".join(f"{x:.1f}" for x in v) + "]"


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

# (external id, title, locations, days ago, skills, work mode or None, remote regions)
FILTER_POSTINGS: list[tuple[str, str, list[str], int, list[str], str | None, list[str]]] = [
    ("globex-1", "Backend Engineer", ["Remote - Worldwide"], 0,
     ["python", "postgresql", "docker"], "remote", ["Worldwide"]),
    ("globex-2", "Platform Engineer", ["Remote - US"], 1,
     ["python", "kubernetes"], "remote", ["US"]),
    ("globex-3", "Site Reliability Engineer", ["Remote - APAC"], 1,
     ["kubernetes", "terraform"], "remote", ["APAC"]),
    ("globex-4", "Backend Engineer Intern", ["Bangalore, India"], 2,
     ["python"], "onsite", []),
    ("globex-5", "Python Developer", ["London, UK"], 3,
     ["python", "django"], None, []),
    ("globex-6", "Data Analyst", ["Berlin, Germany"], 6,
     ["sql", "tableau"], "hybrid", []),
]  # fmt: skip


async def _board(session: AsyncSession, ats: str, token: str, name: str) -> WatchedBoard:
    board = await session.scalar(
        select(WatchedBoard).where(WatchedBoard.ats == ats, WatchedBoard.board_token == token)
    )
    if board is None:
        board = WatchedBoard(ats=ats, board_token=token, display_name=name, origin="demo")
        session.add(board)
    board.active = True
    await session.flush()
    return board


async def _add(
    session: AsyncSession,
    board: WatchedBoard,
    company: str,
    ext: str,
    title: str,
    locations: list[str],
    days: int,
    body: str,
    extracted: dict[str, Any],
    vector: str,
) -> None:
    if await session.scalar(select(Posting.id).where(Posting.external_id == ext)):
        return
    html = f"<p>{body}</p>"
    description = html_to_text(html)
    digest = hashlib.sha256(description.encode()).hexdigest()
    p = Posting(
        source=board.ats,
        external_id=ext,
        watched_board_id=board.id,
        company_name_raw=company,
        title_raw=title,
        locations=locations,
        posted_at=datetime.now(UTC) - timedelta(days=days),
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
        {"v": vector, "id": p.id},
    )


async def load() -> str:
    async with get_sessionmaker()() as session:
        boards = {a: await _board(session, a, TOKEN, COMPANY) for a in ("greenhouse", "lever")}
        for ats, ext, title, locations, days, body, cosine in POSTINGS:
            remote = "Remote" in locations[0]
            extracted: dict[str, Any] = {
                "skills": ["python", "postgresql"] if "Backend" in title else ["kafka", "spark"],
                "seniority": "junior" if "Junior" in title else "senior",
                "work_mode": "remote" if remote else "onsite",
                "remote_regions": ["India"] if remote else [],
                "direct": [],
            }
            vector = _vector(math.acos(min(1.0, cosine)))
            await _add(session, boards[ats], COMPANY, ext, title, locations, days, body,
                       extracted, vector)  # fmt: skip
        globex = await _board(session, "ashby", FILTER_TOKEN, FILTER_COMPANY)
        for k, (ext, title, locations, days, skills, mode, regions) in enumerate(FILTER_POSTINGS):
            extracted = {
                "skills": skills,
                "seniority": "intern" if "Intern" in title else "mid",
                "work_mode": mode or "unknown",
                "remote_regions": regions,
                "direct": [],
            }
            body = f"{title} at {FILTER_COMPANY}. Skills: {', '.join(skills)}."
            await _add(session, globex, FILTER_COMPANY, ext, title, locations, days, body,
                       extracted, _axis(k))  # fmt: skip
        await session.commit()
        counts = await dedup(session)
        listed = await refilter_from_profile(session)
        await session.commit()
    return (
        f"Loaded {COMPANY} and {FILTER_COMPANY}: {counts.placed} postings placed, "
        f"{counts.merged} merged, {counts.new_listings} listings, "
        f"{counts.possible_duplicates} possible duplicate(s); {listed} match your profile"
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
                "DELETE FROM company c WHERE c.name = ANY(:names) AND NOT EXISTS "
                "(SELECT 1 FROM listing l WHERE l.company_id = c.id)"
            ),
            {"names": [COMPANY, FILTER_COMPANY]},
        )
        await session.commit()
    return (
        f"Removed {COMPANY} and {FILTER_COMPANY}: "
        f"{len(postings)} postings, {len(listing_ids)} listings"
    )


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "load"
    print(asyncio.run(load() if action == "load" else clear()))
