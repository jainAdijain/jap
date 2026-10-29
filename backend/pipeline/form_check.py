"""Weekly live form test (Phase 8, PRD Risks: "adapters tested against a live form weekly").

For each ATS, the newest open posting on a board I watch has its application form read
with the same reader apply-listing uses. Nothing is filled or submitted. A form that can't
be read, or that has no fields JAP recognises, is a failure: it is kept in form_check with
a screenshot, shown on the Status card, and emailed as an alert.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import FormCheck, Posting, WatchedBoard
from pipeline.apply.form import FormError, read_form
from schemas.job import ATS

TRIES = 2  # one more try before a network blip counts as a broken form


async def targets(session: AsyncSession) -> dict[str, str | None]:
    """The posting to test for each ATS (newest open, on an active board that isn't demo),
    or None when I watch no open job on it."""
    out: dict[str, str | None] = {}
    for ats in ATS:
        posting_id = await session.scalar(
            select(Posting.id)
            .join(WatchedBoard, WatchedBoard.id == Posting.watched_board_id)
            .where(
                Posting.source == ats.value,
                Posting.state == "open",
                WatchedBoard.active,
                WatchedBoard.origin != "demo",
            )
            .order_by(Posting.posted_at.desc().nulls_last(), Posting.first_seen_at.desc())
            .limit(1)
        )
        out[ats.value] = str(posting_id) if posting_id else None
    return out


async def check(session: AsyncSession, ats: str, posting_id: str) -> FormCheck:
    posting = await session.get(Posting, uuid.UUID(posting_id))
    assert posting is not None
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    shot = Path(get_settings().jap_data_dir) / "form_checks" / f"{ats}-{stamp}.png"
    row = FormCheck(ats=ats, posting_id=posting.id, url=posting.apply_url, ok=False)
    for _ in range(TRIES):
        try:
            read = await read_form(posting.apply_url, shot)
        except FormError as exc:
            row.error = str(exc)[:1000]
            row.screenshot_path = str(shot) if shot.exists() else None
            continue
        row.ok, row.error = True, None
        row.fields = len(read.fields)
        row.required = sum(1 for f in read.fields if f.required)
        if read.captcha:
            row.error = "The form shows a CAPTCHA (jobs on it stop at Needs input)"
        break
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def latest(session: AsyncSession) -> list[FormCheck]:
    """The last test of each ATS."""
    out = []
    for ats in ATS:
        row = await session.scalar(
            select(FormCheck)
            .where(FormCheck.ats == ats.value)
            .order_by(FormCheck.created_at.desc())
            .limit(1)
        )
        if row is not None:
            out.append(row)
    return out
