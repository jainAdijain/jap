"""Unfollow and re-follow (PRD 1.20, I10 to I18): one test per case in the PRD.

Unfollowing closes the board's open postings as unfollowed and never deletes; following
again reopens and updates the same rows at the next search.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import select

from api.runs import extracting
from database.models import WatchedBoard
from database.session import get_sessionmaker
from functions.fetch_board import close_step, fetch_step
from pipeline.apply.flow import why_not
from pipeline.dedup import dedup
from pipeline.enrich import embed_posting, extract_posting
from pipeline.ingest import store_jobs
from pipeline.matching import refilter_from_profile
from pipeline.runs import embeddings_pending
from pipeline.unfollow import close_leftovers
from sources import greenhouse
from tests import boards as b

GH = "https://boards-api.greenhouse.io/v1/boards"
URL = f"{GH}/discord/jobs"
DISCORD = {"ats": "greenhouse", "board_token": "discord", "display_name": "Discord"}
LEAD = b.GREENHOUSE_JOB  # Commercial Policy Lead, id 8806482002
ANALYST = b.job(LEAD, id=1002, title="Policy Analyst", requisition_id="R-2")
COUNSEL = b.job(LEAD, id=1003, title="Product Counsel", requisition_id="R-3")
DESIGNER = b.job(LEAD, id=1004, title="Brand Designer", requisition_id="R-4")
NEW_TEXT = "&lt;p&gt;A rewritten description.&lt;/p&gt;"


async def _follow(api: httpx.AsyncClient, board: dict[str, str] = DISCORD) -> str:
    resp = await api.post("/api/boards", json=board)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _unfollow(api: httpx.AsyncClient, board_id: str) -> None:
    assert (await api.delete(f"/api/boards/{board_id}")).status_code == 204


async def _search(board_id: str) -> dict[str, Any]:
    """What a search does for one board: fetch, close what is gone, dedup."""
    since = datetime.now(UTC) - timedelta(minutes=1)
    fetched = await fetch_step(board_id)
    if not fetched.get("unfollowed"):
        fetched["closed"] = await close_step(board_id, fetched["seen"])
    async with get_sessionmaker()() as session:
        await dedup(session, since=since)
    return fetched


def _read_all(sql: psycopg.Connection[Any]) -> None:
    """As if the AI had read every posting and every job matched my preferences."""
    sql.execute("UPDATE posting SET extracted_hash = description_hash")
    sql.execute("UPDATE listing SET matched = '{role}'")


def _postings(sql: psycopg.Connection[Any]) -> dict[str, tuple[str, str | None]]:
    rows = sql.execute("SELECT title_raw, state, close_reason FROM posting").fetchall()
    return {title: (state, reason) for title, state, reason in rows}


def _listings(sql: psycopg.Connection[Any]) -> dict[str, str]:
    return dict(sql.execute("SELECT title_display, status FROM listing").fetchall())


async def _titles(api: httpx.AsyncClient, status: str = "open") -> list[str]:
    page = (await api.get("/api/listings", params={"status": status})).json()
    return sorted(r["title"] for r in page["rows"])


async def test_unfollow_closes_postings_and_jobs_and_deletes_nothing(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {URL: b.greenhouse(LEAD, ANALYST)})
    board_id = await _follow(api)
    await _search(board_id)
    _read_all(sql)
    assert await _titles(api) == ["Commercial Policy Lead", "Policy Analyst"]

    await _unfollow(api, board_id)
    assert _postings(sql) == {
        "Commercial Policy Lead": ("closed", "unfollowed"),
        "Policy Analyst": ("closed", "unfollowed"),
    }  # I10: closed with the reason, both rows still there
    assert sql.execute("SELECT count(*) FROM posting WHERE closed_at IS NULL").fetchone() == (0,)
    assert set(_listings(sql).values()) == {"closed"}  # I11
    assert await _titles(api) == []
    assert sql.execute("SELECT active FROM watched_board").fetchone() == (False,)


async def test_confirmation_counts_and_changes_nothing(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {URL: b.greenhouse(LEAD, ANALYST, COUNSEL)})
    board_id = await _follow(api)
    await _search(board_id)
    sql.execute("UPDATE listing SET matched = '{role}'")
    sql.execute("UPDATE listing SET status = 'applied' WHERE title_display = 'Policy Analyst'")
    sql.execute(
        "UPDATE posting SET extracted_hash = description_hash WHERE title_raw = 'Policy Analyst'"
    )

    effect = (await api.get(f"/api/boards/{board_id}/unfollow")).json()
    assert effect == {"display_name": "Discord", "hidden": 2, "kept": 1, "unread": 2}  # I15
    assert sql.execute("SELECT count(*) FROM posting WHERE state = 'open'").fetchone() == (3,)
    assert sql.execute("SELECT active FROM watched_board").fetchone() == (True,)
    missing = await api.get("/api/boards/00000000-0000-0000-0000-000000000000/unfollow")
    assert missing.status_code == 404


async def test_jobs_with_an_application_stay_and_hidden_stay_hidden(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {URL: b.greenhouse(LEAD, ANALYST, COUNSEL, DESIGNER)})
    board_id = await _follow(api)
    await _search(board_id)
    _read_all(sql)
    before = {
        "Commercial Policy Lead": "applied",
        "Policy Analyst": "needs_input",
        "Product Counsel": "skipped",
        "Brand Designer": "failed",
    }
    for title, status in before.items():
        sql.execute("UPDATE listing SET status = %s WHERE title_display = %s", (status, title))

    await _unfollow(api, board_id)
    assert _listings(sql) == before  # I12: never closed or reset
    # Sent, Failed and in-progress jobs stay on Jobs; the hidden one stays out of sight.
    assert await _titles(api, "all") == [
        "Brand Designer",
        "Commercial Policy Lead",
        "Policy Analyst",
    ]
    waiting = sql.execute(
        "SELECT id FROM listing WHERE title_display = 'Policy Analyst'"
    ).fetchone()
    assert waiting is not None
    detail = (await api.get(f"/api/listings/{waiting[0]}")).json()
    assert detail["status"] == "needs_input" and detail["unfollowed"] is True  # the note

    # Following again and searching: still Sent, never offered again; still hidden.
    await _follow(api)
    await _search(board_id)
    assert _listings(sql) == before
    sent = sql.execute(
        "SELECT id FROM listing WHERE title_display = 'Commercial Policy Lead'"
    ).fetchone()
    assert sent is not None
    async with get_sessionmaker()() as session:
        assert await why_not(session, sent[0]) == "The job is applied"
    detail = (await api.get(f"/api/listings/{waiting[0]}")).json()
    assert detail["unfollowed"] is False


async def test_a_job_on_two_boards_stays_open_while_one_is_followed(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    twin = b.job(LEAD, id=7001)  # the same role on the company's second board
    b.fake_boards(
        monkeypatch, {URL: b.greenhouse(LEAD), f"{GH}/discordjobs/jobs": b.greenhouse(twin)}
    )
    first = await _follow(api)
    second = await _follow(api, {**DISCORD, "board_token": "discordjobs"})
    await _search(first)
    await _search(second)
    _read_all(sql)
    assert sql.execute("SELECT count(*) FROM listing").fetchone() == (1,)

    effect = (await api.get(f"/api/boards/{first}/unfollow")).json()
    assert effect["hidden"] == 0
    await _unfollow(api, first)
    assert _listings(sql) == {"Commercial Policy Lead": "not_applied"}  # I11
    assert await _titles(api) == ["Commercial Policy Lead"]
    primary = sql.execute(
        "SELECT p.external_id, p.state FROM listing l JOIN posting p ON p.id = l.primary_posting_id"
    ).fetchone()
    assert primary == ("7001", "open")  # the posting on the board I still follow

    await _unfollow(api, second)
    assert _listings(sql) == {"Commercial Policy Lead": "closed"}


async def test_no_ai_reading_for_an_unfollowed_company(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {URL: b.greenhouse(LEAD, ANALYST)})
    board_id = await _follow(api)
    queued = (await _search(board_id))["to_enrich"]
    assert len(queued) == 2
    async with get_sessionmaker()() as session:
        assert await extracting(session) == 2

    await _unfollow(api, board_id)
    async with get_sessionmaker()() as session:
        assert await extracting(session) == 0  # I13: the counter ends
        assert await embeddings_pending(session) == 0
        # The reads queued before the unfollow end here: no model is called (none is set up,
        # so a call would fail the test).
        for posting_id in queued:
            assert (await extract_posting(session, uuid.UUID(posting_id))).skipped is True
            assert await embed_posting(session, uuid.UUID(posting_id)) is False
    assert sql.execute("SELECT count(*) FROM llm_call").fetchone() == (0,)
    assert sql.execute("SELECT count(*) FROM posting WHERE extracted IS NOT NULL").fetchone() == (
        0,
    )


async def test_counter_ignores_boards_unfollowed_before_this_rule(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live's case: a board switched off the old way, its postings left open and unread."""
    b.fake_boards(monkeypatch, {URL: b.greenhouse(LEAD, ANALYST)})
    board_id = await _follow(api)
    await _search(board_id)
    sql.execute("UPDATE watched_board SET active = false")
    async with get_sessionmaker()() as session:
        assert await extracting(session) == 0

    report = await close_leftovers(apply=False)
    assert "Would close 2 postings of 1 unfollowed companies" in report
    assert sql.execute("SELECT count(*) FROM posting WHERE state = 'open'").fetchone() == (2,)
    report = await close_leftovers(apply=True)
    assert "Closed 2 postings of 1 unfollowed companies" in report
    assert set(_postings(sql).values()) == {("closed", "unfollowed")}
    assert set(_listings(sql).values()) == {"closed"}
    assert "Nothing to do" in await close_leftovers(apply=True)


async def test_a_running_search_cannot_reopen_an_unfollowed_board(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {URL: b.greenhouse(LEAD, ANALYST, COUNSEL)})
    board_id = await _follow(api)
    await _search(board_id)
    await _unfollow(api, board_id)

    # The search had already listed this board when I unfollowed it (I14).
    fetched = await fetch_step(board_id)
    assert fetched["unfollowed"] is True and fetched["to_enrich"] == [] and fetched["new"] == 0
    # Or it had already read the board and was about to store the jobs.
    jobs = [greenhouse.to_job(j, "Discord") for j in (LEAD, ANALYST, DESIGNER)]
    async with get_sessionmaker()() as session:
        assert await store_jobs(session, uuid.UUID(board_id), jobs) is None
    assert await close_step(board_id, []) == 0
    assert sql.execute("SELECT count(*) FROM posting").fetchone() == (3,)  # no Brand Designer
    assert set(_postings(sql).values()) == {("closed", "unfollowed")}


async def test_follow_again_reopens_and_updates_the_same_rows(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    routes: b.Routes = {URL: b.greenhouse(LEAD, ANALYST, COUNSEL, DESIGNER)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _follow(api)
    await _search(board_id)
    _read_all(sql)
    # Brand Designer was not read yet when I unfollowed.
    sql.execute("UPDATE posting SET extracted_hash = NULL WHERE title_raw = 'Brand Designer'")
    ids = dict(sql.execute("SELECT title_raw, id FROM posting").fetchall())
    listing_ids = dict(sql.execute("SELECT title_display, id FROM listing").fetchall())
    await _unfollow(api, board_id)

    # Meanwhile the employer changes one description, takes one job down and adds one.
    engineer = b.job(LEAD, id=1005, title="Security Engineer", requisition_id="R-5")
    routes[URL] = b.greenhouse(LEAD, b.job(ANALYST, content=NEW_TEXT), DESIGNER, engineer)

    assert await _follow(api) == board_id  # I16: the same board, switched back on
    assert set(_postings(sql).values()) == {("closed", "unfollowed")}  # nothing until a search
    assert sql.execute("SELECT count(*) FROM watched_board").fetchone() == (1,)

    found = await _search(board_id)
    assert _postings(sql) == {
        "Commercial Policy Lead": ("open", None),  # unchanged: reopened, reason cleared
        "Policy Analyst": ("open", None),  # changed: reopened with the new text
        "Product Counsel": ("closed", "gone_from_board"),  # taken down while I was away
        "Brand Designer": ("open", None),
        "Security Engineer": ("open", None),  # new since I unfollowed
    }
    # I17: reopened is counted apart from new.
    assert (found["new"], found["reopened"], found["changed"], found["unchanged"]) == (1, 3, 1, 2)
    assert found["closed"] == 0  # Product Counsel was already closed
    after = dict(sql.execute("SELECT title_raw, id FROM posting").fetchall())
    assert {t: after[t] for t in ids} == ids  # the same rows
    # Read again: only the changed one, the one never read, and the new one.
    assert set(found["to_enrich"]) == {
        str(after["Policy Analyst"]),
        str(after["Brand Designer"]),
        str(after["Security Engineer"]),
    }
    text = sql.execute(
        "SELECT description_text FROM posting WHERE title_raw = 'Policy Analyst'"
    ).fetchone()
    assert text == ("A rewritten description.",)
    kept = sql.execute(
        "SELECT extracted_hash = description_hash FROM posting "
        "WHERE title_raw = 'Commercial Policy Lead'"
    ).fetchone()
    assert kept == (True,)  # the stored reading is reused

    statuses = _listings(sql)
    assert statuses.pop("Product Counsel") == "closed"
    assert set(statuses.values()) == {"not_applied"}
    now = dict(sql.execute("SELECT title_display, id FROM listing").fetchall())
    assert {t: now[t] for t in listing_ids} == listing_ids  # jobs reopen, none is recreated


async def test_a_repost_under_a_new_id_joins_its_old_job_after_following_again(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    routes: b.Routes = {URL: b.greenhouse(LEAD)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _follow(api)
    await _search(board_id)
    [(listing_id,)] = sql.execute("SELECT id FROM listing").fetchall()
    sql.execute("UPDATE listing SET status = 'applied'")
    await _unfollow(api, board_id)

    routes[URL] = b.greenhouse(b.job(LEAD, id=9999, requisition_id=None))
    await _follow(api)
    await _search(board_id)
    assert sql.execute("SELECT id, status FROM listing").fetchall() == [(listing_id, "applied")]
    assert sorted(_postings_by_id(sql)) == [
        ("8806482002", "closed", "gone_from_board"),
        ("9999", "open", None),
    ]
    async with get_sessionmaker()() as session:
        assert await why_not(session, listing_id) == "The job is applied"  # D4


def _postings_by_id(sql: psycopg.Connection[Any]) -> list[tuple[str, str, str | None]]:
    return sql.execute("SELECT external_id, state, close_reason FROM posting").fetchall()


async def test_reopened_jobs_are_matched_against_my_current_preferences(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {URL: b.greenhouse(LEAD)})
    board_id = await _follow(api)
    await _search(board_id)
    profile = {"target_roles": ["Commercial Policy Lead"], "match_rule": [["role"]]}
    assert (await api.put("/api/profile", json=profile)).status_code == 200
    async with get_sessionmaker()() as session:
        await refilter_from_profile(session)
        await session.commit()
    assert await _titles(api) == ["Commercial Policy Lead"]

    await _unfollow(api, board_id)
    profile["target_roles"] = ["Staff Nurse"]
    assert (await api.put("/api/profile", json=profile)).status_code == 200
    await _follow(api)
    await _search(board_id)
    async with get_sessionmaker()() as session:  # the search's filter step
        await refilter_from_profile(session)
        await session.commit()
    assert _listings(sql) == {"Commercial Policy Lead": "not_applied"}
    assert sql.execute("SELECT matched FROM listing").fetchone() == (None,)
    assert await _titles(api) == []


async def test_following_and_unfollowing_repeatedly_is_safe_and_fetches_nothing(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    routes: b.Routes = {URL: b.greenhouse(LEAD)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _follow(api)
    await _search(board_id)
    _read_all(sql)

    def no_fetch(*args: Any, **kwargs: Any) -> None:
        calls.append("fetch")
        raise AssertionError("nothing is fetched until the next search")

    monkeypatch.setattr("sources.base.client", no_fetch)
    for _ in range(3):
        await _unfollow(api, board_id)
        await _unfollow(api, board_id)  # twice in a row
        assert await _follow(api) == board_id
        assert (await api.post("/api/boards", json=DISCORD)).status_code == 409
    assert calls == []
    assert _postings(sql) == {"Commercial Policy Lead": ("closed", "unfollowed")}
    assert sql.execute("SELECT count(*) FROM posting").fetchone() == (1,)
    assert sql.execute("SELECT count(*) FROM llm_call").fetchone() == (0,)
    async with get_sessionmaker()() as session:
        boards = list(await session.scalars(select(WatchedBoard)))
    assert len(boards) == 1 and boards[0].active is True


async def test_a_closed_posting_always_carries_a_reason(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    routes: b.Routes = {URL: b.greenhouse(LEAD, ANALYST)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _follow(api)
    await _search(board_id)
    routes[URL] = b.greenhouse(LEAD)
    assert (await _search(board_id))["closed"] == 1
    assert _postings(sql) == {
        "Commercial Policy Lead": ("open", None),
        "Policy Analyst": ("closed", "gone_from_board"),  # I6
    }
    with pytest.raises(psycopg.errors.CheckViolation):
        sql.execute("UPDATE posting SET state = 'closed' WHERE state = 'open'")
