"""Apply in Review mode: the database steps of apply-listing and the API around them.

The browser is replaced by stubs here (tests/test_apply_form.py drives a real one), and the
model by the OpenRouter stub from test_llm_call, which fails if it sees a personal value.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

import llm.client as client
from database.session import get_sessionmaker
from pipeline import notify
from pipeline.apply import flow
from pipeline.apply.form import FormField, FormRead, SubmitResult
from tests.test_llm_call import Stub

FORM = [
    FormField("first", "First Name", "text", True),
    FormField("last", "Last Name", "text", True),
    FormField("email", "Email", "email", True),
    FormField("resume", "Resume/CV", "file", True),
    FormField("linkedin", "LinkedIn Profile", "text", False),
    FormField("hear", "How did you hear about us?", "select", False, ["LinkedIn", "Friend"]),
]
WHY = FormField("why", "Why do you want to work at Acme?", "textarea", True, max_chars=600)
NOTICE = FormField("notice", "What is your notice period?", "text", True)
AUTH = FormField("auth", "Are you legally authorized to work in India?", "radio", True,
                 ["Yes", "No"])  # fmt: skip
RESUME_TEXT = "Asha Rao\nBackend engineer. Python, FastAPI, Postgres. Built a payments API."


def _profile(sql: psycopg.Connection[Any], cap: int = 20) -> None:
    sql.execute(
        "INSERT INTO profile (full_name, email, phone, links, target_roles, locations, daily_cap)"
        " VALUES ('Asha Rao', 'asha.rao@example.com', '+91 98765 43210', %s,"
        " '{Backend Engineer}', '{Bangalore}', %s)",
        (json.dumps([{"label": "LinkedIn", "url": "https://linkedin.com/in/asharao"}]), cap),
    )


def _resume(sql: psycopg.Connection[Any], data_dir: Path) -> str:
    path = data_dir / "resume.pdf"
    path.write_bytes(b"%PDF-1.4 resume")
    row = sql.execute(
        "INSERT INTO resume_version (file_path, file_sha256, original_name, mime, text, is_active)"
        " VALUES (%s, %s, 'Asha-Rao.pdf', 'application/pdf', %s, true) RETURNING id",
        (str(path), "0" * 64, RESUME_TEXT),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _listing(
    sql: psycopg.Connection[Any], title: str = "Backend Engineer", ext: str = "1",
    company: str = "Acme",
) -> str:  # fmt: skip
    """A listed, Not applied listing with one open Greenhouse posting."""
    row = sql.execute(
        "INSERT INTO company (name, canonical_name) VALUES (%s, lower(%s))"
        " ON CONFLICT (canonical_name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (company, company),
    ).fetchone()
    assert row is not None
    company_id = row[0]
    board = sql.execute(
        "INSERT INTO watched_board (ats, board_token, display_name, origin, company_id)"
        " VALUES ('greenhouse', lower(%s), %s, 'user', %s)"
        " ON CONFLICT (ats, board_token) DO UPDATE SET active = true RETURNING id",
        (company, company, company_id),
    ).fetchone()
    assert board is not None
    role = title.lower()
    listing = sql.execute(
        "INSERT INTO listing (company_id, canonical_role, title_display, matched, last_posted_at)"
        " VALUES (%s, %s, %s, '{role}', now()) RETURNING id",
        (company_id, role, title),
    ).fetchone()
    assert listing is not None
    posting = sql.execute(
        "INSERT INTO posting (source, external_id, watched_board_id, listing_id, company_name_raw,"
        " title_raw, canonical_role, posting_url, apply_url, description_html, description_text,"
        " description_hash, raw_payload, locations) VALUES ('greenhouse', %s, %s, %s, %s, %s, %s,"
        " %s, %s, '<p>Build payment APIs.</p>', 'Build payment APIs in Python.', 'h', '{}',"
        " '{Bangalore}') RETURNING id",
        (
            ext,
            board[0],
            listing[0],
            company,
            title,
            role,
            f"https://example.com/jobs/{ext}",
            f"https://example.com/jobs/{ext}/apply",
        ),  # fmt: skip
    ).fetchone()
    assert posting is not None
    sql.execute(
        "UPDATE listing SET primary_posting_id = %s WHERE id = %s", (posting[0], listing[0])
    )
    return str(listing[0])


@pytest.fixture
def setup(db: str, sql: psycopg.Connection[Any], tmp_path: Path) -> dict[str, Any]:
    _profile(sql)
    return {"resume": _resume(sql, tmp_path), "listing": _listing(sql), "data": tmp_path}


def _fake_form(
    monkeypatch: pytest.MonkeyPatch, fields: list[FormField], captcha: bool = False
) -> None:
    async def read(url: str, screenshot: Path | None = None) -> FormRead:
        return FormRead(fields=list(fields), captcha=captcha)

    monkeypatch.setattr(flow, "read_form", read)


async def _prepare(listing_id: str) -> str:
    """create-attempt, snapshot, read-form, map-fields, as apply-listing runs them."""
    async with get_sessionmaker()() as session:
        app_id = await flow.create_attempt(session, listing_id, None)
        url = await flow.snapshot(session, app_id)
        await flow.read_and_save(session, app_id, url)
        await flow.map_fields(session, app_id)
    return app_id


def _status(sql: psycopg.Connection[Any], app_id: str) -> tuple[str, str]:
    row = sql.execute(
        "SELECT a.status, l.status FROM application a JOIN listing l ON l.id = a.listing_id"
        " WHERE a.id = %s",
        (app_id,),
    ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


async def test_apply_now_stops_at_ready_for_review(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    resp = await api.post(f"/api/listings/{setup['listing']}/apply")
    assert resp.status_code == 202
    assert events == [("listing/apply.requested", {"listing_id": setup["listing"], "run_id": None})]

    _fake_form(monkeypatch, FORM)
    submitted: list[str] = []

    async def no_submit(*args: Any) -> SubmitResult:
        submitted.append("yes")
        return SubmitResult(confirmed=True, screenshot=False, clicked=True)

    monkeypatch.setattr(flow, "submit_form", no_submit)
    app_id = await _prepare(setup["listing"])
    assert _status(sql, app_id) == ("ready_for_review", "ready_for_review")
    assert submitted == []  # Review mode: nothing is submitted before Approve

    detail = (await api.get(f"/api/listings/{setup['listing']}")).json()
    app = detail["application"]
    got = {f["label"]: (f["value"], f["filled_from"]) for f in app["fields"]}
    assert got == {
        "First Name": ("Asha", "profile:first_name"),
        "Last Name": ("Rao", "profile:last_name"),
        "Email": ("asha.rao@example.com", "profile:email"),
        "Resume/CV": ("Asha-Rao.pdf", "resume"),
        "LinkedIn Profile": ("https://linkedin.com/in/asharao", "profile:linkedin"),
        "How did you hear about us?": ("", ""),  # optional and not in the bank: left empty
    }
    assert app["jd"]["text"] == "Build payment APIs in Python." and app["jd"]["captured_at"]
    assert detail["status"] == "ready_for_review"
    assert detail["apply_blocked"] == "This job is already being prepared"
    again = await api.post(f"/api/listings/{setup['listing']}/apply")
    assert again.status_code == 409


async def test_edit_skip_and_undo(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    edited = await api.put(f"/api/applications/{app_id}", json={"answers": {"hear": "Friend"}})
    assert edited.status_code == 200
    hear = next(f for f in edited.json()["fields"] if f["key"] == "hear")
    assert (hear["value"], hear["filled_from"]) == ("Friend", "you")
    bad = await api.put(f"/api/applications/{app_id}", json={"answers": {"nope": "x"}})
    assert bad.status_code == 422

    skipped = await api.post(f"/api/listings/{setup['listing']}/skip")
    assert skipped.json()["status"] == "skipped"
    assert _status(sql, app_id) == ("cancelled", "skipped")
    assert events[-1] == ("application/cancelled", {"listing_id": setup["listing"]})
    assert (await api.post(f"/api/applications/{app_id}/approve")).status_code == 409
    assert (await api.post(f"/api/listings/{setup['listing']}/apply")).status_code == 409
    undone = await api.post(f"/api/listings/{setup['listing']}/unskip")
    assert undone.json()["status"] == "not_applied" and undone.json()["apply_blocked"] is None


async def test_required_free_text_is_drafted_from_the_jd(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    _fake_form(monkeypatch, [*FORM, WHY])
    draft = "I want to build Acme's payment APIs in Python, as I did for [NAME]'s last team."
    stub = Stub([json.dumps({"answer": draft, "needs_my_fact": False, "missing_fact": ""})])
    monkeypatch.setattr(client, "chat", stub)
    app_id = await _prepare(setup["listing"])
    assert _status(sql, app_id)[0] == "ready_for_review"
    body = stub.bodies[0]["messages"][1]["content"]
    assert "Why do you want to work at Acme?" in body and "Build payment APIs" in body
    assert "Company: Acme" in body and "Role: Backend Engineer" in body
    app = (await api.get(f"/api/applications/{app_id}")).json()
    why = next(f for f in app["fields"] if f["key"] == "why")
    assert why["filled_from"] == "drafted"
    assert why["value"] == draft.replace("[NAME]", "Asha Rao")  # restored locally
    bank = sql.execute("SELECT count(*) FROM answer_bank").fetchone()
    assert bank == (0,)  # a draft is not saved unless I save it


async def test_facts_only_i_know_go_to_needs_input_and_the_answer_bank(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    _fake_form(monkeypatch, [*FORM, NOTICE, AUTH])
    monkeypatch.setattr(client, "chat", Stub([]))  # no model call for these
    app_id = await _prepare(setup["listing"])
    assert _status(sql, app_id) == ("needs_input", "needs_input")
    app = (await api.get(f"/api/applications/{app_id}")).json()
    assert [q["key"] for q in app["pending"]] == ["notice", "auth"]
    assert app["pending"][1]["options"] == ["Yes", "No"]

    partial = await api.post(
        f"/api/applications/{app_id}/answer", json={"answers": {"notice": "30 days"}}
    )
    assert partial.status_code == 422
    resp = await api.post(
        f"/api/applications/{app_id}/answer",
        json={"answers": {"notice": "30 days", "auth": "Yes"}, "save_to_bank": True},
    )
    assert resp.status_code == 200 and resp.json()["status"] == "preparing"
    assert events[-1] == ("application/answered", {"application_id": app_id})
    bank = (await api.get("/api/answers")).json()
    assert {(a["question"], a["answer"]) for a in bank} == {
        ("What is your notice period?", "30 days"),
        ("Are you legally authorized to work in India?", "Yes"),
    }
    async with get_sessionmaker()() as session:
        mapped = await flow.map_fields(session, app_id)  # back to step 5 with my answers
    assert mapped.status == "ready_for_review"

    # The next job asking the same questions is filled from the answer bank.
    other = _listing(sql, "Platform Engineer", ext="2")
    app2 = await _prepare(other)
    assert _status(sql, app2)[0] == "ready_for_review"
    fields = {f["key"]: f for f in (await api.get(f"/api/applications/{app2}")).json()["fields"]}
    assert (fields["notice"]["value"], fields["notice"]["filled_from"]) == (
        "30 days",
        "answer_bank",
    )
    assert fields["auth"]["value"] == "Yes"


async def test_captcha_and_missing_files_block(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    _fake_form(monkeypatch, [*FORM, FormField("t", "Transcript", "file", True)], captcha=True)
    app_id = await _prepare(setup["listing"])
    app = (await api.get(f"/api/applications/{app_id}")).json()
    assert app["status"] == "needs_input"
    assert [(q["key"], q["blocked"]) for q in app["pending"]] == [("captcha", True), ("t", True)]
    resp = await api.post(f"/api/applications/{app_id}/answer", json={"answers": {}})
    assert resp.status_code == 409


async def test_approve_submits_once_and_freezes(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    calls: list[dict[str, Any]] = []

    def write_shot(shot: Path) -> None:
        shot.parent.mkdir(parents=True, exist_ok=True)
        shot.write_bytes(b"png")

    async def fake_submit(
        url: str, fields: list[FormField], answers: dict[str, str], files: dict[str, Any],
        shot: Path, trace: Path,
    ) -> SubmitResult:  # fmt: skip
        calls.append({"url": url, "answers": answers, "files": sorted(files)})
        write_shot(shot)
        return SubmitResult(confirmed=True, screenshot=True, clicked=True)

    monkeypatch.setattr(flow, "submit_form", fake_submit)
    async with get_sessionmaker()() as session:
        with pytest.raises(flow.Stop):  # not approved yet: submit refuses
            await flow.submit(session, app_id)
    assert calls == []

    resp = await api.post(f"/api/applications/{app_id}/approve")
    assert resp.status_code == 200 and resp.json()["status"] == "applying"
    assert events[-1] == ("application/approved", {"application_id": app_id})
    async with get_sessionmaker()() as session:
        assert await flow.submit(session, app_id) == {"status": "applied"}
    assert calls[0]["url"] == "https://example.com/jobs/1/apply"
    assert calls[0]["files"] == ["resume"]
    assert calls[0]["answers"]["email"] == "asha.rao@example.com"
    assert _status(sql, app_id) == ("applied", "applied")

    detail = (await api.get(f"/api/listings/{setup['listing']}")).json()
    assert detail["apply_blocked"] == "The job is applied"
    app = detail["application"]
    assert (
        app["submitted_at"]
        and app["has_screenshot"]
        and app["resume"]["original_name"] == "Asha-Rao.pdf"
    )
    shot = await api.get(f"/api/files/screenshot/{app_id}")
    assert shot.status_code == 200 and shot.content == b"png"
    with pytest.raises(psycopg.errors.RaiseException):  # A2: frozen after submit
        sql.execute("UPDATE application SET answers = '{}' WHERE id = %s", (app_id,))
    assert (await api.post(f"/api/listings/{setup['listing']}/apply")).status_code == 409
    assert (await api.post(f"/api/listings/{setup['listing']}/skip")).status_code == 409

    # Reposted under a new id, the job joins the same listing (one per company and role)
    # and becomes its primary posting: still Applied, and a run does not pick it.
    sql.execute(
        "INSERT INTO posting (source, external_id, watched_board_id, listing_id, company_name_raw,"
        " title_raw, posting_url, apply_url, description_html, description_text,"
        " description_hash, raw_payload) SELECT source, '1-repost', watched_board_id, listing_id,"
        " company_name_raw, title_raw, posting_url || '?r', apply_url || '?r', description_html,"
        " description_text, 'h2', '{}' FROM posting WHERE listing_id = %s",
        (setup["listing"],),
    )
    sql.execute(
        "UPDATE listing SET primary_posting_id = (SELECT id FROM posting WHERE external_id ="
        " '1-repost') WHERE id = %s",
        (setup["listing"],),
    )
    run = (await api.post("/api/runs", json={"mode": "find_and_apply"})).json()
    async with get_sessionmaker()() as session:
        assert await flow.pick(session, run["id"]) == []
    sql.execute("UPDATE listing SET status = 'not_applied' WHERE id = %s", (setup["listing"],))
    async with get_sessionmaker()() as session:  # even if its status were reset somehow
        assert await flow.why_not(session, uuid.UUID(setup["listing"])) == (
            "You already applied for this job"
        )
        assert await flow.pick(session, run["id"]) == []


async def test_submit_never_presses_twice(
    setup: dict[str, Any],
    sql: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    sql.execute(
        "UPDATE application SET status = 'applying', submit_started_at = now() WHERE id = %s",
        (app_id,),
    )

    async def must_not_run(*args: Any) -> SubmitResult:
        raise AssertionError("pressed Submit again")

    monkeypatch.setattr(flow, "submit_form", must_not_run)
    async with get_sessionmaker()() as session:
        assert await flow.submit(session, app_id) == {"status": "failed"}
    row = sql.execute("SELECT error FROM application WHERE id = %s", (app_id,)).fetchone()
    assert row is not None and str(row[0]).startswith("Submit outcome unknown")
    assert _status(sql, app_id) == ("failed", "failed")


async def test_unconfirmed_submit_fails_and_unpressed_submit_retries(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    sql.execute("UPDATE application SET status = 'applying' WHERE id = %s", (app_id,))

    async def not_clicked(*args: Any) -> SubmitResult:
        return SubmitResult(confirmed=False, screenshot=False, clicked=False, error="timeout")

    monkeypatch.setattr(flow, "submit_form", not_clicked)
    async with get_sessionmaker()() as session:
        with pytest.raises(flow.SubmitRetry):
            await flow.submit(session, app_id)
    row = sql.execute(
        "SELECT submit_started_at FROM application WHERE id = %s", (app_id,)
    ).fetchone()
    assert row == (None,)  # safe to retry: Submit was never pressed

    async def unconfirmed(*args: Any) -> SubmitResult:
        return SubmitResult(confirmed=False, screenshot=False, clicked=True, error="no thanks page")

    monkeypatch.setattr(flow, "submit_form", unconfirmed)
    async with get_sessionmaker()() as session:
        assert await flow.submit(session, app_id) == {"status": "failed"}
    detail = (await api.get(f"/api/listings/{setup['listing']}")).json()
    assert detail["status"] == "failed" and detail["application"]["error"] == "no thanks page"
    assert detail["apply_blocked"] is None
    retry = await api.post(f"/api/applications/{app_id}/retry")
    assert retry.status_code == 202


async def test_cap_and_gap(setup: dict[str, Any], sql: psycopg.Connection[Any]) -> None:
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        assert await flow.wait_before_submit(session, now) is None
    other = _listing(sql, "Platform Engineer", ext="2")
    sql.execute(
        "INSERT INTO application (listing_id, posting_id, status, mode, resume_version_id,"
        " submitted_at) SELECT id, primary_posting_id, 'applied', 'review', %s, %s"
        " FROM listing WHERE id = %s",
        (setup["resume"], now - timedelta(seconds=10), other),
    )
    async with get_sessionmaker()() as session:
        until = await flow.wait_before_submit(session, now)
    assert until is not None and abs((until - now).total_seconds() - 20) < 1  # 30 s apart (A1)
    sql.execute("UPDATE profile SET daily_cap = 1")
    async with get_sessionmaker()() as session:
        until = await flow.wait_before_submit(session, now)
        picked_room = await flow.applied_today(session, now)
    assert until is not None and until > now + timedelta(minutes=1)  # tomorrow
    assert picked_room == 1


async def test_find_and_apply_picks_within_the_cap(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient,
    events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    _listing(sql, "Platform Engineer", ext="2")
    skipped = _listing(sql, "Data Engineer", ext="3")
    sql.execute("UPDATE listing SET status = 'skipped' WHERE id = %s", (skipped,))
    run = (await api.post("/api/runs", json={"mode": "find_and_apply"})).json()
    assert run["mode"] == "find_and_apply"
    async with get_sessionmaker()() as session:
        picked = await flow.pick(session, run["id"])
    assert len(picked) == 2 and skipped not in picked
    sql.execute("UPDATE profile SET daily_cap = 1")
    async with get_sessionmaker()() as session:
        assert len(await flow.pick(session, run["id"])) == 1
    scoped = await api.post(
        "/api/runs", json={"mode": "find_and_apply", "roles": ["Platform Engineer"]}
    )
    assert scoped.json()["id"] == run["id"]  # merged into the queued run
    sql.execute("UPDATE profile SET daily_cap = 20")
    sql.execute('UPDATE run SET scope = \'{"roles": ["Platform Engineer"]}\'')
    async with get_sessionmaker()() as session:
        only = await flow.pick(session, run["id"])
    assert len(only) == 1


async def test_find_only_run_joins_upgrade_to_find_and_apply(
    db: str, api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]]
) -> None:
    first = (await api.post("/api/runs", json={"mode": "find_only"})).json()
    second = (await api.post("/api/runs", json={"mode": "find_and_apply"})).json()
    assert second["id"] == first["id"] and second["mode"] == "find_and_apply"


async def test_apply_needs_a_resume(
    db: str, sql: psycopg.Connection[Any], api: httpx.AsyncClient
) -> None:
    _profile(sql)
    listing = _listing(sql)
    resp = await api.post(f"/api/listings/{listing}/apply")
    assert resp.status_code == 409 and "resume" in resp.json()["error"]["message"]


async def test_confirmation_email_once(
    setup: dict[str, Any],
    sql: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_form(monkeypatch, FORM)
    app_id = await _prepare(setup["listing"])
    sql.execute(
        "UPDATE application SET status = 'applied', submitted_at = now() WHERE id = %s", (app_id,)
    )
    async with get_sessionmaker()() as session:
        mail = await notify.build(session, uuid.UUID(app_id))
    assert mail is not None
    assert mail.subject == "Applied: Backend Engineer at Acme"
    assert mail.to == "asha.rao@example.com"
    assert "Original posting: https://example.com/jobs/1" in mail.body
    assert f"/?listing={setup['listing']}" in mail.body and "Status: Applied" in mail.body

    sent: list[notify.Mail] = []

    async def fake_send(m: notify.Mail) -> None:
        sent.append(m)

    monkeypatch.setattr(notify, "send", fake_send)
    from functions.notify_applied import mark_step, send_step

    assert await send_step(app_id) is True
    await mark_step(app_id)
    assert await send_step(app_id) is False  # E2: exactly once
    assert len(sent) == 1
    row = sql.execute("SELECT email_sent_at FROM application WHERE id = %s", (app_id,)).fetchone()
    assert row is not None and row[0] is not None  # E3, and allowed on a frozen row
