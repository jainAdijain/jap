"""Automatic at volume (V2 to V7, PRD 1.29): random picks spread across companies, limits
per company and per board, a hold on a board that answers 429 or 403, and the jobs that
Automatic mode must never send on its own (checker flags, forms that ban AI help)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from config import get_settings
from database.models import AtsHold
from database.session import get_sessionmaker
from pipeline import claude_writer
from pipeline.apply import flow, pacing
from pipeline.apply.form import FormError, FormField, FormRead, SubmitResult
from tests.test_apply import (
    FORM,
    WHY,
    _fake_form,
    _listing,
    _prepare,
    _profile,
    _resume,
    _status,
)


@pytest.fixture
def setup(db: str, sql: psycopg.Connection[Any], tmp_path: Path) -> dict[str, Any]:
    _profile(sql)
    return {"resume": _resume(sql, tmp_path), "listing": _listing(sql), "data": tmp_path}


def _automatic(sql: psycopg.Connection[Any]) -> None:
    sql.execute("UPDATE profile SET apply_mode = 'automatic', must_have_skills = '{}'")
    sql.execute("UPDATE listing SET work_mode = 'remote'")


async def _pick(api: httpx.AsyncClient) -> list[str]:
    run = (await api.post("/api/runs", json={"mode": "find_and_apply"})).json()
    async with get_sessionmaker()() as session:
        return await flow.pick(session, run["id"])


def _companies(sql: psycopg.Connection[Any], ids: list[str]) -> list[str]:
    rows = sql.execute(
        "SELECT c.name FROM listing l JOIN company c ON c.id = l.company_id"
        " WHERE l.id = ANY(%s::uuid[])",
        (ids,),
    ).fetchall()
    return sorted(str(r[0]) for r in rows)


async def test_automatic_picks_at_most_two_per_company_in_random_order(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient,
    events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    for i in range(2, 6):
        _listing(sql, f"Engineer {i}", ext=str(i))  # five at Acme in all
    _listing(sql, "Engineer", ext="b1", company="Beta")
    _automatic(sql)
    picked = await _pick(api)
    assert _companies(sql, picked) == ["Acme", "Acme", "Beta"]  # V3
    # Review mode keeps the plain newest-first pick, with no company limit.
    sql.execute("UPDATE profile SET apply_mode = 'review'")
    assert len(await _pick(api)) == 6


async def test_attempts_started_today_count_towards_the_company_limit(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient,
    events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    _listing(sql, "Engineer 2", ext="2")
    _listing(sql, "Engineer 3", ext="3")
    _automatic(sql)
    sql.execute(
        "INSERT INTO application (listing_id, posting_id, status, mode, resume_version_id)"
        " SELECT id, primary_posting_id, 'failed', 'automatic', %s FROM listing WHERE id = %s",
        (setup["resume"], setup["listing"]),
    )
    sql.execute("UPDATE listing SET status = 'failed' WHERE id = %s", (setup["listing"],))
    assert len(await _pick(api)) == 1  # one attempt at Acme today already


async def test_the_board_limit_and_a_board_on_hold(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient,
    events: list[tuple[str, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    for i, company in enumerate(("Beta", "Gamma", "Delta")):
        _listing(sql, "Engineer", ext=f"x{i}", company=company)
    _automatic(sql)
    monkeypatch.setattr(get_settings(), "jap_per_ats_day", 2)
    assert len(await _pick(api)) == 2  # V4: four Greenhouse jobs, two a day
    sql.execute(
        "INSERT INTO ats_hold (ats, until, strikes)"
        " VALUES ('greenhouse', now() + interval '1 hour', 1)"
    )
    assert await _pick(api) == []  # V5: nothing picked on a board on hold


async def test_extra_picks_past_the_cap_less_what_is_in_flight(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient,
    events: list[tuple[str, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    for i in range(8):
        _listing(sql, "Engineer", ext=f"c{i}", company=f"Company {i}")
    _automatic(sql)
    sql.execute("UPDATE profile SET daily_cap = 3")
    monkeypatch.setattr(get_settings(), "jap_auto_extra_picks", 2)
    assert len(await _pick(api)) == 5  # V7: the cap of 3 plus 2
    sql.execute(
        "INSERT INTO application (listing_id, posting_id, status, mode, resume_version_id)"
        " SELECT id, primary_posting_id, 'applying', 'automatic', %s FROM listing WHERE id = %s",
        (setup["resume"], setup["listing"]),
    )
    sql.execute("UPDATE listing SET status = 'applying' WHERE id = %s", (setup["listing"],))
    assert len(await _pick(api)) == 4


async def test_submits_on_one_board_are_spaced_and_a_hold_grows(
    setup: dict[str, Any], sql: psycopg.Connection[Any]
) -> None:
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        assert await pacing.ats_wait(session, "greenhouse", now) is None
    other = _listing(sql, "Engineer", ext="2", company="Beta")
    sql.execute(
        "INSERT INTO application (listing_id, posting_id, status, mode, resume_version_id,"
        " submitted_at) SELECT id, primary_posting_id, 'applied', 'automatic', %s, %s"
        " FROM listing WHERE id = %s",
        (setup["resume"], now - timedelta(seconds=60), other),
    )
    async with get_sessionmaker()() as session:
        until = await pacing.ats_wait(session, "greenhouse", now)
        assert until is not None and abs((until - now).total_seconds() - 240) < 1  # 5 min (V4)
        assert await pacing.ats_wait(session, "ashby", now) is None  # another board is free

        first = await pacing.record_block(session, "greenhouse", "The form page answered 429")
        await session.commit()
        assert timedelta(minutes=59) < first - now < timedelta(minutes=61)  # 1 hour (V5)
        assert await pacing.ats_wait(session, "greenhouse", now) == first
        again = await pacing.record_block(session, "greenhouse", "429 again")  # still on hold
        assert again == first
        hold = await session.get(AtsHold, "greenhouse")
        assert hold is not None
        hold.until = now - timedelta(seconds=1)  # the hour passed, and it refused again
        second = await pacing.record_block(session, "greenhouse", "The form page answered 403")
        assert timedelta(hours=3, minutes=59) < second - now < timedelta(hours=4, minutes=1)
        await pacing.clear_strikes(session, "greenhouse")
        assert hold.strikes == 0


def test_blocked_messages() -> None:
    assert pacing.is_blocked("The form page answered 429")
    assert pacing.is_blocked("The form page answered 403")
    assert pacing.is_blocked('The employer\'s site turned the submission away: "Too many requests"')
    assert not pacing.is_blocked("The form page answered 404")
    assert not pacing.is_blocked("No confirmation after submit")
    assert not pacing.is_blocked(None)


async def test_a_429_at_submit_holds_the_board_and_the_job_waits(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    sql.execute("UPDATE application SET status = 'applying' WHERE id = %s", (app_id,))

    async def refused(*args: Any, **kwargs: Any) -> SubmitResult:
        return SubmitResult(False, False, False, error="The form page answered 429")

    monkeypatch.setattr(flow, "submit_form", refused)
    async with get_sessionmaker()() as session:
        result = await flow.submit(session, app_id)
    assert "wait_until" in result
    row = sql.execute(
        "SELECT status, submit_started_at FROM application WHERE id = %s", (app_id,)
    ).fetchone()
    assert row == ("applying", None)  # not failed, nothing pressed
    held = sql.execute("SELECT ats, strikes FROM ats_hold").fetchall()
    assert held == [("greenhouse", 1)]
    async with get_sessionmaker()() as session:  # the next try waits for the hold
        again = await flow.submit(session, app_id)
    assert again["wait_until"] == result["wait_until"]


async def test_a_429_while_reading_the_form_is_a_board_block(
    setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def blocked(url: str, screenshot: Path | None = None) -> FormRead:
        raise FormError("The form page answered 429")

    monkeypatch.setattr(flow, "read_form", blocked)
    async with get_sessionmaker()() as session:
        app_id = await flow.create_attempt(session, setup["listing"], None)
        url = await flow.snapshot(session, app_id)
        with pytest.raises(flow.BoardBlocked):
            await flow.read_and_save(session, app_id, url)
        until = await flow.board_hold(session, app_id, "The form page answered 429")
        assert until is not None
        assert await flow.board_hold(session, app_id) == until


AI_BAN = FormField(
    "ai", "Please do not use AI tools such as ChatGPT to write your answers.", "checkbox", False
)


@pytest.mark.parametrize(
    "text",
    [
        "Please do not use AI tools such as ChatGPT to write your answers.",
        "Applications written with generative AI are not permitted.",
        "AI-generated applications will be rejected.",
    ],
)
def test_ai_ban_wording(text: str) -> None:
    assert flow.AI_BAN.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "You will build AI products for our customers.",
        "Experience with LLMs and generative AI is a plus.",
        "Do not apply if you need visa sponsorship. We use AI daily.",
    ],
)
def test_not_an_ai_ban(text: str) -> None:
    assert not flow.AI_BAN.search(text)


async def test_automatic_mode_holds_a_form_that_bans_ai(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _automatic(sql)
    _fake_form(monkeypatch, FORM)
    plain = await _prepare(setup["listing"])
    assert _status(sql, plain)[0] == "applying"  # nothing unclear: it goes on to submit
    other = _listing(sql, "Engineer", ext="2", company="Beta")
    sql.execute("UPDATE listing SET work_mode = 'remote'")
    _fake_form(monkeypatch, [*FORM, AI_BAN])
    banned = await _prepare(other)
    row = sql.execute(
        "SELECT status, review_reason FROM application WHERE id = %s", (banned,)
    ).fetchone()
    assert row is not None and row[0] == "ready_for_review" and "AI" in row[1]


async def test_the_writer_answers_automatic_jobs_and_a_flag_holds_one(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    _automatic(sql)
    _fake_form(monkeypatch, [*FORM, WHY])
    app_id = await _prepare(setup["listing"])
    assert _status(sql, app_id)[0] == "needs_input"  # waiting for the Claude writer
    async with get_sessionmaker()() as session:
        out = await claude_writer.export(session)
    assert [j["application_id"] for j in out["jobs"]] == [app_id]  # V6: Automatic too
    assert "_review" in out["review_rule"]

    async with get_sessionmaker()() as session:
        lines = await claude_writer.import_answers(
            session, {app_id: {"why": "I build payment APIs.", "_review": "Claims Kafka"}}
        )
    assert lines == [f"{app_id}: preparing"]
    async with get_sessionmaker()() as session:
        await flow.map_fields(session, app_id)  # apply-listing maps again after the answer
    row = sql.execute(
        "SELECT status, review_reason, answers ? '_review' FROM application WHERE id = %s",
        (app_id,),
    ).fetchone()
    assert row == ("ready_for_review", "Checker: Claims Kafka", False)
