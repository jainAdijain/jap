"""Storing what a board returned (I2, I5, I6).

Every Job becomes a posting row keyed on (source, external_id). The raw payload and the
HTML are stored unchanged; description_text is the HTML stripped to plain text, and its
hash decides whether the posting needs extracting again (I4). A posting that is no longer
on its board is marked Closed, never deleted.
"""

import hashlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Posting
from schemas.job import Job

BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Stored:
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    reopened: int = 0
    to_enrich: list[str] = field(default_factory=list)  # posting ids whose text changed


def _row(job: Job, board_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    text = html_to_text(job.description_html)
    return {
        "source": job.source.value,
        "external_id": job.external_id,
        "watched_board_id": board_id,
        "company_name_raw": job.company_name,
        "title_raw": job.title,
        "requisition_id": job.requisition_id,
        "locations": job.locations,
        "department": job.department,
        "employment_type": job.employment_type,
        "posted_at": job.posted_at,
        "posting_url": str(job.posting_url),
        "apply_url": str(job.apply_url),
        "description_html": job.description_html,
        "description_text": text,
        "description_hash": text_hash(text),
        "raw_payload": job.raw_payload,
        "state": "open",
        "last_seen_at": now,
        "closed_at": None,
    }


async def store_jobs(session: AsyncSession, board_id: uuid.UUID, jobs: list[Job]) -> Stored:
    """Upsert every job on (source, external_id) and say which need extracting."""
    out = Stored()
    if not jobs:
        return out
    now = datetime.now(UTC)
    rows = {(j.source.value, j.external_id): _row(j, board_id, now) for j in jobs}
    existing = {
        (p.source, p.external_id): p
        for p in await session.scalars(
            select(Posting).where(
                Posting.source == jobs[0].source.value,
                Posting.external_id.in_([key[1] for key in rows]),
            )
        )
    }
    for key, row in rows.items():
        old = existing.get(key)
        if old is None:
            out.new += 1
        elif old.description_hash != row["description_hash"]:
            out.changed += 1
        else:
            out.unchanged += 1
        if old is not None and old.state == "closed":
            out.reopened += 1
        ins = insert(Posting).values(**row)
        stmt = ins.on_conflict_do_update(
            index_elements=["source", "external_id"],
            set_={k: ins.excluded[k] for k in row if k not in ("source", "external_id")},
        ).returning(Posting.id, Posting.extracted_hash, Posting.description_hash)
        posting_id, extracted_hash, description_hash = (await session.execute(stmt)).one()
        if extracted_hash != description_hash:
            out.to_enrich.append(str(posting_id))
    await session.commit()
    return out


async def close_missing(
    session: AsyncSession,
    board_id: uuid.UUID,
    seen_external_ids: list[str],
    only: Callable[[str], bool] | None = None,
) -> int:
    """Mark Closed every open posting of this board that the board no longer lists (I6).

    only (a test on the title) limits it to the postings a scoped run looked at (T3)."""
    where = [Posting.watched_board_id == board_id, Posting.state == "open"]
    if seen_external_ids:
        where.append(Posting.external_id.not_in(seen_external_ids))
    if only is not None:
        rows = await session.execute(select(Posting.id, Posting.title_raw).where(*where))
        ids = [pid for pid, title in rows if only(title)]
        if not ids:
            return 0
        where.append(Posting.id.in_(ids))
    stmt = update(Posting).where(*where).values(state="closed", closed_at=func.now())
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
