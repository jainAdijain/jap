import uuid
from typing import Any

import psycopg
import pytest

from database.models import Posting, WatchedBoard
from database.session import get_sessionmaker
from functions.fetch_board import close_step, fetch_step
from pipeline.ingest import html_to_text
from schemas.job import Job
from sources import greenhouse
from tests import boards as b

GH = "https://boards-api.greenhouse.io/v1/boards"
SECOND = b.job(b.GREENHOUSE_JOB, id=1002, title="Policy Analyst")


def test_html_to_text() -> None:
    html = "<div><p>Hello&nbsp;there</p><ul><li>One</li><li>Two</li></ul><script>x()</script></div>"
    assert html_to_text(html) == "Hello there\n\n- One\n\n- Two"


async def _board(ats: str = "greenhouse", token: str = "discord") -> str:
    async with get_sessionmaker()() as session:
        board = WatchedBoard(ats=ats, board_token=token, display_name="Discord", origin="user")
        session.add(board)
        await session.commit()
        return str(board.id)


def _job() -> Job:
    return greenhouse.to_job(b.GREENHOUSE_JOB, "Discord")


async def test_fetch_stores_postings_and_queues_extraction(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    routes: b.Routes = {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB, SECOND)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _board()

    first = await fetch_step(board_id)
    assert (first["new"], first["changed"], first["unchanged"]) == (2, 0, 0)
    assert len(first["to_enrich"]) == 2
    row = sql.execute(
        "SELECT title_raw, description_html, description_text, raw_payload->>'id', state "
        "FROM posting WHERE external_id = '8806482002'"
    ).fetchone()
    assert row is not None
    title, html, text, raw_id, state = row
    assert title == "Commercial Policy Lead" and state == "open"
    assert html.startswith("<div><p>Discord")  # stored unchanged (I5)
    assert "- Own commercial policy" in text and "<" not in text
    assert raw_id == "8806482002"
    assert sql.execute("SELECT last_ok FROM watched_board").fetchone() == (True,)

    # Mark both extracted: an unchanged re-fetch then queues nothing (I4).
    sql.execute("UPDATE posting SET extracted_hash = description_hash")
    again = await fetch_step(board_id)
    assert (again["new"], again["unchanged"], again["to_enrich"]) == (0, 2, [])
    assert sql.execute("SELECT count(*) FROM posting").fetchone() == (2,)

    # A changed description is queued again.
    routes[f"{GH}/discord/jobs"] = b.greenhouse(
        b.job(b.GREENHOUSE_JOB, content="&lt;p&gt;New text&lt;/p&gt;"), SECOND
    )
    changed = await fetch_step(board_id)
    assert changed["changed"] == 1 and len(changed["to_enrich"]) == 1


async def test_missing_postings_are_closed_not_deleted(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    routes: b.Routes = {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB, SECOND)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _board()
    await fetch_step(board_id)

    routes[f"{GH}/discord/jobs"] = b.greenhouse(b.GREENHOUSE_JOB)
    fetched = await fetch_step(board_id)
    assert await close_step(board_id, fetched["seen"]) == 1
    assert sql.execute(
        "SELECT external_id, state, closed_at IS NOT NULL FROM posting ORDER BY external_id"
    ).fetchall() == [("1002", "closed", True), ("8806482002", "open", False)]

    # Back on the board: reopened, same row.
    routes[f"{GH}/discord/jobs"] = b.greenhouse(b.GREENHOUSE_JOB, SECOND)
    back = await fetch_step(board_id)
    assert back["reopened"] == 1
    assert sql.execute("SELECT count(*) FROM posting WHERE state = 'open'").fetchone() == (2,)


async def test_board_not_found_is_marked_and_does_not_raise(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": 404})
    board_id = await _board()
    result = await fetch_step(board_id)
    assert result["ok"] is False and "not found" in result["error"]
    assert sql.execute("SELECT last_ok FROM watched_board").fetchone() == (False,)


async def test_posting_maps_back_to_job(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB)})
    await fetch_step(await _board())
    async with get_sessionmaker()() as session:
        from sqlalchemy import select

        p = await session.scalar(select(Posting))
        assert p is not None
        job = Job(
            source=p.source, external_id=p.external_id, company_name=p.company_name_raw,
            title=p.title_raw, locations=p.locations, department=p.department,
            employment_type=p.employment_type, posted_at=p.posted_at,
            posting_url=p.posting_url, apply_url=p.apply_url,
            requisition_id=p.requisition_id, description_html=p.description_html,
            raw_payload=p.raw_payload,
        )  # fmt: skip
    assert job.model_dump(exclude={"posted_at"}) == _job().model_dump(exclude={"posted_at"})
    assert uuid.UUID(str(p.id))
