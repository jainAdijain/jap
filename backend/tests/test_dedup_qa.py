"""The dedup QA set (PRD QA and testing): about 100 labelled job pairs, placed with the real
dedup (canonical company and role, requisition ID, near match in pgvector) in the test
database. Each pair starts from an empty database. `make qa` runs this file and prints the
table; CI fails when accuracy drops below the bar or any two different jobs are merged.
"""

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from sqlalchemy import select, text

from database.models import PossibleDuplicate, Posting, WatchedBoard
from database.session import get_sessionmaker
from pipeline.dedup import dedup
from tests.conftest import TABLES

PAIRS = Path(__file__).parent / "qa" / "dedup_pairs.yaml"
MIN_ACCURACY = 0.95
DIM = 768


@dataclass
class Side:
    company: str
    title: str
    locations: list[str]
    req: str | None


def side(spec: str) -> Side:
    parts = [p.strip() for p in spec.split("|")]
    company, title, locations = parts[0], parts[1], parts[2]
    req = parts[3] if len(parts) > 3 else None
    return Side(company, title, [x.strip() for x in locations.split(";")], req)


def vector(cosine: float) -> str:
    """A unit vector whose cosine with the base vector [1, 0, ...] is `cosine`."""
    angle = math.acos(max(-1.0, min(1.0, cosine)))
    v = [0.0] * DIM
    v[0], v[1] = math.cos(angle), math.sin(angle)
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


async def place(a: Side, b: Side, cosine: float) -> str:
    """'same', 'flagged' (a Possible duplicate for me to decide) or 'apart'."""
    async with get_sessionmaker()() as session:
        ids = []
        both = ((a, "greenhouse", vector(1.0)), (b, "lever", vector(cosine)))
        for i, (s, ats, vec) in enumerate(both):
            board = WatchedBoard(
                ats=ats, board_token=f"qa-{i}", display_name=s.company, origin="user"
            )
            session.add(board)
            await session.flush()
            body = f"{s.title} at {s.company}"
            digest = hashlib.sha256(body.encode()).hexdigest()
            p = Posting(
                source=ats, external_id=f"qa-{i}", watched_board_id=board.id,
                company_name_raw=s.company, title_raw=s.title, locations=s.locations,
                requisition_id=s.req, posted_at=datetime.now(UTC) - timedelta(days=2 - i),
                posting_url=f"https://example.com/qa/{i}", apply_url=f"https://example.com/qa/{i}",
                description_html=f"<p>{body}</p>", description_text=body, description_hash=digest,
                raw_payload={"qa": True}, extracted={"direct": []}, extracted_hash=digest,
                embedded_hash=digest,
            )  # fmt: skip
            session.add(p)
            await session.flush()
            await session.execute(
                text("UPDATE posting SET embedding = CAST(:v AS vector) WHERE id = :id"),
                {"v": vec, "id": p.id},
            )
            ids.append(p.id)
        await session.commit()
        await dedup(session)
        listings = [
            await session.scalar(select(Posting.listing_id).where(Posting.id == i)) for i in ids
        ]
        if listings[0] == listings[1]:
            return "same"
        flagged = await session.scalar(select(PossibleDuplicate.id).limit(1))
        return "flagged" if flagged else "apart"


@pytest.fixture
def pairs() -> list[dict[str, Any]]:
    data = yaml.safe_load(PAIRS.read_text())["pairs"]
    assert len(data) >= 100
    return list(data)


async def test_dedup_qa_set(
    db: str, sql: psycopg.Connection[Any], pairs: list[dict[str, Any]]
) -> None:
    rows = []
    for pair in pairs:
        sql.execute(f"TRUNCATE {TABLES} CASCADE")
        got = await place(side(pair["a"]), side(pair["b"]), float(pair["cos"]))
        rows.append((pair, got))
    same = [g for p, g in rows if p["same"]]
    different = [g for p, g in rows if not p["same"]]
    right = sum(g == "same" for g in same) + sum(g != "same" for g in different)
    false_merges = [p for p, g in rows if not p["same"] and g == "same"]
    missed = [p for p, g in rows if p["same"] and g != "same"]
    accuracy = right / len(rows)
    print(f"\nDedup QA set: {len(rows)} pairs ({len(same)} same, {len(different)} different)")
    print(f"  accuracy {accuracy:.1%} (bar {MIN_ACCURACY:.0%})")
    print(f"  same pairs merged {sum(g == 'same' for g in same)}/{len(same)}, "
          f"flagged as Possible duplicate {sum(g == 'flagged' for g in same)}, "
          f"kept apart {sum(g == 'apart' for g in same)}")  # fmt: skip
    print(f"  different pairs merged by mistake {len(false_merges)}/{len(different)}, "
          f"flagged {sum(g == 'flagged' for g in different)}")  # fmt: skip
    for p in missed:
        print(f"  missed: {p['a']}  ~  {p['b']}  (cos {p['cos']})")
    for p in false_merges:
        print(f"  FALSE MERGE: {p['a']}  ~  {p['b']}  (cos {p['cos']})")
    assert not false_merges, "two different jobs were merged into one listing"
    assert accuracy >= MIN_ACCURACY
