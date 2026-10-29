"""Phase 5 triggers: the Run dialog's scope, repeats collapsing, the Daily run and the
profile save's background run (T1 to T5)."""

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from database.session import get_sessionmaker
from functions.fetch_board import close_step, fetch_step
from functions.triggers import daily_due, daily_step, queue_step
from pipeline.runs import in_scope, merge_scope, role_matcher, start_run
from tests import boards as b
from tests.test_ingest import _board

GH = "https://boards-api.greenhouse.io/v1/boards"
IST = ZoneInfo("Asia/Kolkata")


def test_merge_scope_widens() -> None:
    assert merge_scope({"roles": ["Backend Engineer"]}, {"roles": ["Data Engineer"]}) == {
        "roles": ["Backend Engineer", "Data Engineer"]
    }
    assert merge_scope({"roles": ["Backend Engineer"]}, {}) == {}  # one ask is for everything
    assert merge_scope({}, {}) == {}


def test_role_scope_matches_like_the_match_rule() -> None:
    m = role_matcher(["Backend Engineer"])
    assert in_scope("Sr. Backend Developer (Remote) - Bangalore", m)
    assert in_scope("Senior Backend Engineer", m)
    assert not in_scope("Frontend Engineer", m)
    assert not in_scope("Backend Engineering Manager", m)
    assert role_matcher(None) is None and in_scope("Anything", None)


def test_daily_due() -> None:
    at = time(6, 0)
    six = datetime(2026, 10, 29, 6, 0, tzinfo=IST)
    assert daily_due(six, at, None)
    assert daily_due(six + timedelta(minutes=3), at, None)
    assert not daily_due(six - timedelta(minutes=1), at, None)
    # Missed while the Mac slept: not caught up later (v1 accepts a missed run).
    assert not daily_due(six + timedelta(minutes=30), at, None)
    # Fires once per slot, even when the next tick is still inside the window.
    assert not daily_due(six + timedelta(minutes=1), at, six)
    assert daily_due(six, at, six - timedelta(days=1))


async def test_run_dialog_scope_and_collapse(
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]]
) -> None:
    first = await api.post("/api/runs", json={"roles": ["Backend Engineer", " ", ""]})
    assert first.status_code == 202
    assert first.json()["scope"] == {"roles": ["Backend Engineer"]}
    second = await api.post("/api/runs", json={"roles": ["Data Engineer"]})
    assert second.json()["id"] == first.json()["id"]  # one queued run (T4)
    assert second.json()["scope"] == {"roles": ["Backend Engineer", "Data Engineer"]}
    third = await api.post("/api/runs", json={})
    assert third.json()["id"] == first.json()["id"] and third.json()["scope"] == {}
    assert [e[0] for e in events] == ["run/requested"]

    state = (await api.get("/api/runs/state")).json()
    assert state["last"] is None and state["active"] is None
    assert state["queued"]["id"] == first.json()["id"]


async def test_state_shows_last_active_and_queued(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    sql.execute(
        "INSERT INTO run (trigger, mode, status, created_at, ended_at, counts) VALUES "
        "('scheduled', 'find_only', 'succeeded', now() - interval '2 hours', "
        " now() - interval '119 minutes', '{\"fetched\": 12, \"listed\": 3}'),"
        "('profile_saved', 'find_only', 'running', now() - interval '1 minute', null, '{}')"
    )
    queued = (await api.post("/api/runs", json={})).json()
    state = (await api.get("/api/runs/state")).json()
    assert state["last"]["trigger"] == "scheduled" and state["last"]["counts"]["listed"] == 3
    assert state["active"]["trigger"] == "profile_saved"
    assert state["queued"]["id"] == queued["id"] and queued["trigger"] == "manual"


async def test_scoped_fetch_touches_only_that_role(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = b.job(b.GREENHOUSE_JOB, id=2001, title="Senior Backend Engineer")
    policy = b.GREENHOUSE_JOB  # Commercial Policy Lead
    routes: b.Routes = {f"{GH}/discord/jobs": b.greenhouse(backend, policy)}
    b.fake_boards(monkeypatch, routes)
    board_id = await _board()
    await fetch_step(board_id)  # an unscoped run stores both
    assert sql.execute("SELECT count(*) FROM posting").fetchone() == (2,)

    # Both leave the board; a Backend Engineer run closes only the backend job.
    routes[f"{GH}/discord/jobs"] = b.greenhouse()
    fetched = await fetch_step(board_id, ["Backend Engineer"])
    assert fetched["fetched"] == 0
    assert await close_step(board_id, fetched["seen"], ["Backend Engineer"]) == 1
    states = dict(sql.execute("SELECT title_raw, state FROM posting").fetchall())
    assert states == {"Senior Backend Engineer": "closed", "Commercial Policy Lead": "open"}

    # A scoped fetch stores only that role's jobs.
    new_backend = b.job(b.GREENHOUSE_JOB, id=2002, title="Backend Developer")
    new_policy = b.job(b.GREENHOUSE_JOB, id=2003, title="Policy Analyst")
    routes[f"{GH}/discord/jobs"] = b.greenhouse(policy, new_backend, new_policy)
    fetched = await fetch_step(board_id, ["Backend Engineer"])
    assert fetched["fetched"] == 1 and fetched["new"] == 1
    titles = {t for (t,) in sql.execute("SELECT title_raw FROM posting").fetchall()}
    assert "Backend Developer" in titles and "Policy Analyst" not in titles


async def test_start_run_passes_roles_to_each_board(db: str, sql: psycopg.Connection[Any]) -> None:
    board_id = await _board()
    sql.execute(
        "INSERT INTO run (trigger, mode, scope) VALUES "
        "('manual', 'find_only', '{\"roles\": [\"Backend Engineer\"]}')"
    )
    row = sql.execute("SELECT id FROM run").fetchone()
    assert row is not None
    run_id = row[0]
    async with get_sessionmaker()() as session:
        boards = await start_run(session, run_id)
    assert boards == [{"board_id": board_id, "ats": "greenhouse", "roles": ["Backend Engineer"]}]
    assert sql.execute("SELECT status FROM run").fetchone() == ("running",)


async def test_profile_save_queues_a_background_run(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], events: list[tuple[str, dict[str, Any]]]
) -> None:
    await api.put("/api/profile", json={"target_roles": ["Backend Engineer"]})
    assert events[-1] == ("profile/saved", {})  # the save itself re-filtered (T2)
    run_id = await queue_step("profile_saved")
    assert run_id is not None
    assert await queue_step("profile_saved") is None  # repeats collapse (T4)
    assert sql.execute("SELECT trigger FROM run").fetchall() == [("profile_saved",)]


async def test_daily_step_fires_once_when_on(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    now = datetime.now(IST)
    await api.patch("/api/profile", json={"daily_run_at": now.strftime("%H:%M")})
    assert await daily_step() is None  # Daily run is off
    await api.patch("/api/profile", json={"daily_run": True})
    run_id = await daily_step()
    assert run_id is not None
    assert await daily_step() is None  # the same slot does not fire twice
    assert sql.execute("SELECT trigger, status FROM run").fetchall() == [("scheduled", "queued")]
    last = sql.execute("SELECT daily_run_last_at FROM profile").fetchone()
    assert last is not None and last[0] <= datetime.now(UTC)


async def test_a_queued_run_waits_its_turn(db: str, sql: psycopg.Connection[Any]) -> None:
    """Inngest lets a queued run's first step start beside a running one; it must wait."""
    await _board()
    sql.execute(
        "INSERT INTO run (trigger, mode, status, started_at) "
        "VALUES ('manual', 'find_only', 'running', now())"
    )
    sql.execute("INSERT INTO run (trigger, mode) VALUES ('scheduled', 'find_only')")
    row = sql.execute("SELECT id FROM run WHERE status = 'queued'").fetchone()
    assert row is not None
    async with get_sessionmaker()() as session:
        assert await start_run(session, row[0]) is None
    assert sql.execute("SELECT status FROM run WHERE id = %s", (row[0],)).fetchone() == ("queued",)
    # A run stuck as running for hours (a crash) no longer blocks.
    sql.execute("UPDATE run SET started_at = now() - interval '3 hours' WHERE status = 'running'")
    async with get_sessionmaker()() as session:
        assert await start_run(session, row[0]) is not None
    assert sql.execute("SELECT status FROM run WHERE id = %s", (row[0],)).fetchone() == ("running",)
