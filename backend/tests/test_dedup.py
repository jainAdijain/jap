import json
from typing import Any

import httpx
import psycopg
import pytest

import llm.client as client
from database import demo
from database.session import get_sessionmaker
from functions.fetch_board import fetch_step
from pipeline.dedup import Settled, dedup, settle_duplicates
from tests import boards as b
from tests.test_ingest import _board
from tests.test_llm_call import Stub

GH = "https://boards-api.greenhouse.io/v1/boards"


def _listings(sql: psycopg.Connection[Any]) -> list[tuple[str, int]]:
    """JAP Demo Co's listings (the Globex Demo jobs are there for the filters)."""
    return sql.execute(
        "SELECT l.title_display, count(p.id) FROM listing l JOIN posting p ON p.listing_id = l.id "
        "JOIN company c ON c.id = l.company_id WHERE c.name = %s "
        "GROUP BY l.id ORDER BY l.title_display",
        (demo.COMPANY,),
    ).fetchall()


async def _list_engineers(api: httpx.AsyncClient) -> None:
    """A profile whose rule lists every demo engineering job."""
    saved = await api.put("/api/profile", json={"target_roles": ["Engineer"]})
    assert saved.status_code == 200, saved.text


async def test_demo_groups_postings(db: str, sql: psycopg.Connection[Any]) -> None:
    message = await demo.load()
    assert "12 postings placed, 1 merged, 11 listings, 1 possible duplicate" in message
    assert _listings(sql) == [
        ("Junior Backend Engineer", 1),
        ("Senior Backend Engineer - Bangalore", 2),  # newest posting is primary (D1)
        ("Senior Data Platform Engineer", 1),
        ("Senior Data Platform Engineer, Streaming", 1),
        ("Senior Frontend Engineer", 1),
    ]
    locations = sql.execute(
        "SELECT locations FROM listing WHERE canonical_role = 'senior backend engineer'"
    ).fetchone()
    assert locations == (["Bangalore, India", "Remote (India)"],)  # merged (D3)
    assert sql.execute("SELECT count(*) FROM company").fetchone() == (2,)
    assert sql.execute(
        "SELECT count(*) FROM watched_board WHERE company_id IS NOT NULL"
    ).fetchone() == (3,)
    # Loading twice adds nothing; clearing removes everything.
    assert "0 postings placed" in await demo.load()
    await demo.clear()
    for table in ("posting", "listing", "watched_board", "company", "possible_duplicate"):
        assert sql.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,), table


async def test_merge_and_split_are_remembered(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    await demo.load()
    await _list_engineers(api)
    rows = (await api.get("/api/listings")).json()["rows"]
    by_title = {r["title"]: r for r in rows}
    backend = by_title["Senior Backend Engineer - Bangalore"]
    assert backend["platforms"] == ["greenhouse", "lever"] and backend["postings"] == 2

    detail = (await api.get(f"/api/listings/{backend['id']}")).json()
    assert detail["primary"]["title"] == "Senior Backend Engineer - Bangalore"
    assert [a["title"] for a in detail["also_on"]] == ["Sr. Backend Engineer (Remote)"]
    assert {x["title"] for x in detail["same_company"]} >= {"Senior Frontend Engineer"}

    # Merge Frontend into Backend by hand.
    frontend = by_title["Senior Frontend Engineer"]
    merged = await api.post(
        f"/api/listings/{backend['id']}/merge", json={"other_listing_id": frontend["id"]}
    )
    assert merged.status_code == 200 and len(merged.json()["also_on"]) == 2
    assert (await api.get(f"/api/listings/{frontend['id']}")).status_code == 404

    # Split the Remote posting out.
    remote = next(a for a in merged.json()["also_on"] if a["title"].startswith("Sr."))
    split = await api.post(f"/api/postings/{remote['posting_id']}/split")
    assert split.status_code == 200
    again = await api.post(f"/api/postings/{remote['posting_id']}/split")
    assert again.status_code == 409  # already alone

    # Forget every placement, as if postings were fetched fresh: the choices come back.
    before = _listings(sql)
    sql.execute("UPDATE listing SET primary_posting_id = NULL")
    sql.execute("UPDATE posting SET listing_id = NULL")
    async with get_sessionmaker()() as session:
        counts = await dedup(session)
    assert counts.how["override"] == 2  # the merged and the split posting
    assert sorted(t for t, _ in _listings(sql)) == sorted(t for t, _ in before)


async def test_possible_duplicate_decisions(api: httpx.AsyncClient) -> None:
    await demo.load()
    await _list_engineers(api)
    [pair] = (await api.get("/api/duplicates")).json()
    assert {pair["a"]["title"], pair["b"]["title"]} == {
        "Senior Data Platform Engineer",
        "Senior Data Platform Engineer, Streaming",
    }
    assert 0.75 <= pair["title_similarity"] < 0.9 and pair["description_similarity"] == 0.9
    bad = await api.post(f"/api/duplicates/{pair['id']}", json={"decision": "maybe"})
    assert bad.status_code == 422
    assert (
        await api.post(f"/api/duplicates/{pair['id']}", json={"decision": "merged"})
    ).status_code == 204
    assert (await api.get("/api/duplicates")).json() == []
    titles = [r["title"] for r in (await api.get("/api/listings")).json()["rows"]]
    assert len([t for t in titles if t.startswith("Senior Data Platform")]) == 1
    again = await api.post(f"/api/duplicates/{pair['id']}", json={"decision": "kept_apart"})
    assert again.status_code in (404, 409)


async def test_closed_postings_close_the_listing_and_reposts_rejoin(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    routes: b.Routes = {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _board()
    await fetch_step(board_id)
    async with get_sessionmaker()() as session:
        await dedup(session)
    [(listing_id, status)] = sql.execute("SELECT id, status FROM listing").fetchall()
    assert status == "not_applied"

    from functions.fetch_board import close_step

    routes[f"{GH}/discord/jobs"] = b.greenhouse()
    await close_step(board_id, [])
    async with get_sessionmaker()() as session:
        since = sql.execute("SELECT now() - interval '1 minute'").fetchone()
        assert since is not None
        await dedup(session, since=since[0])
    assert sql.execute("SELECT status FROM listing").fetchone() == ("closed",)

    # A repost months later (new id, same role) joins the old listing (D4).
    routes[f"{GH}/discord/jobs"] = b.greenhouse(
        b.job(b.GREENHOUSE_JOB, id=999, requisition_id=None)
    )
    await fetch_step(board_id)
    async with get_sessionmaker()() as session:
        counts = await dedup(session)
    assert counts.merged == 1 and counts.new_listings == 0
    assert sql.execute("SELECT id, status FROM listing").fetchall() == [(listing_id, "not_applied")]


async def test_requisition_id_joins_different_titles(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    other = b.job(b.GREENHOUSE_JOB, id=2, title="Policy Lead, Commercial")  # same R-107406
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB, other)})
    await fetch_step(await _board())
    async with get_sessionmaker()() as session:
        counts = await dedup(session)
    assert counts.how["requisition"] == 1
    assert sql.execute("SELECT count(*) FROM listing").fetchone() == (1,)


async def test_requisition_id_keeps_seniority_apart(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    senior = b.job(b.GREENHOUSE_JOB, id=2, title="Senior Backend Engineer")  # same R-107406
    staff = b.job(b.GREENHOUSE_JOB, id=3, title="Staff Data Engineer")
    b.fake_boards(
        monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB, senior, staff)}
    )
    await fetch_step(await _board())
    async with get_sessionmaker()() as session:
        counts = await dedup(session)
    assert counts.how["requisition"] == 0
    assert sql.execute("SELECT count(*) FROM listing").fetchone() == (3,)


async def _settle(stub: Stub, monkeypatch: pytest.MonkeyPatch) -> Settled:
    monkeypatch.setattr(client, "chat", stub)
    async with get_sessionmaker()() as session:
        return await settle_duplicates(session)


async def test_the_model_merges_a_possible_duplicate(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await demo.load()
    stub = Stub([json.dumps({"same": True, "reason": "Same team and duties"})])
    assert await _settle(stub, monkeypatch) == Settled(merged=1)
    sent = stub.bodies[0]["messages"][1]["content"]
    assert "Senior Data Platform Engineer" in sent and "Streaming" in sent
    # The merged listing is gone with its pair; the merge is remembered per posting (D5).
    merges = sql.execute("SELECT count(*) FROM dedup_override WHERE kind = 'merge'").fetchone()
    assert merges == (1,)
    assert len([t for t, _ in _listings(sql) if t.startswith("Senior Data Platform")]) == 1
    assert await _settle(Stub([]), monkeypatch) == Settled()  # nothing left to ask


async def test_the_model_keeps_two_jobs_apart(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await demo.load()
    stub = Stub([json.dumps({"same": False, "reason": "Streaming is a different team"})])
    assert await _settle(stub, monkeypatch) == Settled(kept_apart=1)
    assert sql.execute("SELECT decision, scores->>'reason' FROM possible_duplicate").fetchone() == (
        "kept_apart",
        "Streaming is a different team",
    )
    assert len([t for t, _ in _listings(sql) if t.startswith("Senior Data Platform")]) == 2


async def test_a_pair_the_model_cannot_answer_stays_open(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await demo.load()
    assert await _settle(Stub(["Not JSON.", "Still not."]), monkeypatch) == Settled(undecided=1)
    assert sql.execute("SELECT decision FROM possible_duplicate").fetchone() == ("open",)
