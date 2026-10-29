"""Phase 8: Automatic mode, caps, failures with screenshots, alerts, backups and form tests.

The browser and the model are stubbed as in test_apply; the backup test runs the real
pg_dump when the app container has it (CI has no matching client, so it is skipped there).
"""

import gzip
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

import llm.client as client
from database.session import get_sessionmaker
from functions.notify_failure import mark_step, send_step
from functions.triggers import daily_step
from pipeline import alerts, backup, form_check, notify
from pipeline.apply import flow
from pipeline.apply.form import FormError, FormRead, SubmitResult
from tests.test_apply import (
    FORM,
    NOTICE,
    WHY,
    _fake_form,
    _listing,
    _prepare,
    _profile,
    _resume,
    _status,
)  # fmt: skip
from tests.test_cover_letter import LETTER_TEXT
from tests.test_llm_call import Stub

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def setup(db: str, sql: psycopg.Connection[Any], tmp_path: Path) -> dict[str, Any]:
    _profile(sql)
    return {"resume": _resume(sql, tmp_path), "listing": _listing(sql), "data": tmp_path}


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _known_mode(sql: psycopg.Connection[Any], listing_id: str, mode: str = "remote") -> None:
    sql.execute("UPDATE listing SET work_mode = %s WHERE id = %s", (mode, listing_id))


async def test_automatic_mode_applies_unless_unclear(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    await api.patch("/api/profile", json={"apply_mode": "automatic"})
    _fake_form(monkeypatch, FORM)
    clear = setup["listing"]
    _known_mode(sql, clear)
    app_id = await _prepare(clear)
    assert _status(sql, app_id) == ("applying", "applying")  # straight on to submit (A4)

    unclear = _listing(sql, title="Data Engineer", ext="2")  # work mode not stated
    held = await _prepare(unclear)
    assert _status(sql, held) == ("ready_for_review", "ready_for_review")  # A3
    body = (await api.get(f"/api/applications/{held}")).json()
    assert body["mode"] == "automatic" and body["review_reason"].startswith("Work mode unclear")

    # A question only I can answer still waits in Automatic mode.
    _fake_form(monkeypatch, [*FORM, NOTICE])
    third = _listing(sql, title="Platform Engineer", ext="3")
    _known_mode(sql, third)
    asked = await _prepare(third)
    assert _status(sql, asked) == ("needs_input", "needs_input")


async def test_review_mode_still_waits_and_switch_is_per_attempt(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    """A4: an attempt already waiting for review keeps waiting after Automatic is switched on."""
    _fake_form(monkeypatch, FORM)
    _known_mode(sql, setup["listing"])
    app_id = await _prepare(setup["listing"])
    assert _status(sql, app_id)[0] == "ready_for_review"
    await api.patch("/api/profile", json={"apply_mode": "automatic"})
    assert (await api.get(f"/api/applications/{app_id}")).json()["mode"] == "review"
    assert _status(sql, app_id)[0] == "ready_for_review"


async def test_daily_cap_and_gap(
    setup: dict[str, Any], sql: psycopg.Connection[Any], api: httpx.AsyncClient
) -> None:
    patched = await api.patch("/api/profile", json={"daily_cap": 2})
    assert patched.json()["daily_cap"] == 2
    assert (await api.patch("/api/profile", json={"daily_cap": -1})).status_code == 422
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        assert await flow.wait_before_submit(session, now) is None
    for i in range(2):
        other = _listing(sql, title=f"Engineer {i}", ext=f"c{i}")
        sql.execute(
            "INSERT INTO application (listing_id, posting_id, mode, resume_version_id, status,"
            " submitted_at) SELECT l.id, l.primary_posting_id, 'automatic', %s, 'applied', %s"
            " FROM listing l WHERE l.id = %s",
            (setup["resume"], now - timedelta(seconds=10 * (2 - i)), other),
        )
    async with get_sessionmaker()() as session:
        until = await flow.wait_before_submit(session, now)
    assert until is not None and until.astimezone(IST).hour == 0  # tomorrow, after midnight
    await api.patch("/api/profile", json={"daily_cap": 3})
    async with get_sessionmaker()() as session:
        gap = await flow.wait_before_submit(session, now)
    assert gap is not None and gap - now == timedelta(seconds=20)  # 30 s after the last one


async def test_spend_cap_from_profile_stops_letters(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    sql.execute(
        "INSERT INTO llm_call (purpose, model, prompt_version, input_sha256, cost_usd)"
        " VALUES ('answer', 'm', 'v1', %s, 0.06)",
        ("0" * 64,),
    )
    profile = (await api.get("/api/profile")).json()
    assert profile["spend"] == {"month_usd": 0.06, "cap_usd": 2.0, "reached": False}
    lowered = (await api.patch("/api/profile", json={"spend_cap_usd": 0.05})).json()
    assert lowered["spend"]["cap_usd"] == 0.05 and lowered["spend"]["reached"] is True

    stub = Stub([])  # any model call would fail the test: none may be made
    monkeypatch.setattr(client, "chat", stub)
    _fake_form(monkeypatch, [*FORM, WHY, LETTER_TEXT])
    async with get_sessionmaker()() as session:
        app_id = await flow.create_attempt(session, setup["listing"], None)
        url = await flow.snapshot(session, app_id)
        await flow.read_and_save(session, app_id, url)
        mapped = await flow.map_fields(session, app_id)
        assert mapped.status == "cover_letter"
        await flow.write_cover_letter(session, app_id)
    assert stub.bodies == []
    assert _status(sql, app_id)[0] == "needs_input"
    body = (await api.get(f"/api/applications/{app_id}")).json()
    reasons = {q["label"]: q["reason"] for q in body["pending"]}
    assert reasons["Cover Letter"].startswith("Spend cap reached")
    assert "nothing is sent without one" in reasons["Cover Letter"]
    assert "Spend cap reached" in reasons[WHY.label]
    assert body["cover_letter_text"] is None

    back = (await api.patch("/api/profile", json={"spend_cap_usd": 2})).json()
    assert back["spend"] == {"month_usd": 0.06, "cap_usd": 2.0, "reached": False}


async def test_a_broken_form_fails_with_reason_and_screenshot(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    async def broken(url: str, screenshot: Path | None = None) -> FormRead:
        assert screenshot is not None
        _write(screenshot, b"\x89PNG not found page")
        raise FormError("The form page answered 404")

    monkeypatch.setattr(flow, "read_form", broken)
    async with get_sessionmaker()() as session:
        app_id = await flow.create_attempt(session, setup["listing"], None)
        url = await flow.snapshot(session, app_id)
        with pytest.raises(FormError):
            await flow.read_and_save(session, app_id, url)
        # Inngest retries, then apply-listing's on_failure fails the attempt with the reason.
        await flow.fail_by_id(session, app_id, "The form page answered 404")
    detail = (await api.get(f"/api/listings/{setup['listing']}")).json()
    assert detail["status"] == "failed"
    app = detail["application"]
    assert app["error"] == "The form page answered 404" and app["has_screenshot"] is True
    shot = await api.get(f"/api/files/screenshot/{app_id}")
    assert shot.status_code == 200 and shot.content.startswith(b"\x89PNG")
    retry = await api.post(f"/api/applications/{app_id}/retry")
    assert retry.status_code == 202


async def test_alerts_are_sent_once(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[notify.Mail] = []

    async def fake_send(mail: notify.Mail) -> None:
        sent.append(mail)

    monkeypatch.setattr(notify, "send", fake_send)
    run = sql.execute(
        "INSERT INTO run (trigger, mode, status, ended_at, counts) VALUES ('scheduled',"
        " 'find_only', 'succeeded', now(), %s) RETURNING id",
        ('{"boards": 3, "boards_failed": 1, "board_errors": ["acme: HTTP 404"]}',),
    ).fetchone()
    assert run is not None
    ok_run = sql.execute(
        "INSERT INTO run (trigger, mode, status, ended_at, counts) VALUES ('manual',"
        " 'find_only', 'succeeded', now(), '{\"boards\": 3, \"boards_failed\": 0}') RETURNING id"
    ).fetchone()
    assert ok_run is not None

    assert await send_step({"run_id": str(ok_run[0])}) is None  # nothing failed
    kind = await send_step({"run_id": str(run[0])})
    assert kind == "run"
    await mark_step("run", str(run[0]))
    assert await send_step({"run_id": str(run[0])}) is None  # sent once
    assert sent[0].subject == "JAP alert: 1 board failed" and "acme: HTTP 404" in sent[0].body
    assert sent[0].to == "asha.rao@example.com"

    async def no_form(url: str, screenshot: Path | None = None) -> FormRead:
        raise FormError("No application form found on the page")

    monkeypatch.setattr(flow, "read_form", no_form)
    async with get_sessionmaker()() as session:
        app_id = await flow.create_attempt(session, setup["listing"], None)
        await flow.fail_by_id(session, app_id, "No application form found on the page")
    assert await send_step({"application_id": app_id}) == "application"
    await mark_step("application", app_id)
    assert await send_step({"application_id": app_id}) is None
    mail = sent[1]
    assert mail.subject == "JAP alert: apply failed: Backend Engineer at Acme"
    assert "No application form found" in mail.body and "Retry" in mail.body
    for m in sent:  # alerts carry no answers or personal details beyond the address
        assert "Asha" not in m.body and "98765" not in m.body


async def test_failed_run_alert(db: str, sql: psycopg.Connection[Any]) -> None:
    sql.execute("INSERT INTO profile (email) VALUES ('me@example.com')")
    row = sql.execute(
        "INSERT INTO run (trigger, mode, status, ended_at, error) VALUES ('manual',"
        " 'find_and_apply', 'failed', now(), 'The run before this one did not finish') RETURNING id"
    ).fetchone()
    assert row is not None
    async with get_sessionmaker()() as session:
        mail = await alerts.run_alert(session, row[0])
    assert mail is not None and mail.subject == "JAP alert: a run failed"
    assert "did not finish" in mail.body and "the Run button" in mail.body


async def test_daily_run_applies_in_automatic_mode(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    now = datetime.now(IST)
    await api.patch(
        "/api/profile",
        json={"daily_run": True, "daily_run_at": now.strftime("%H:%M"), "apply_mode": "automatic"},
    )
    assert await daily_step() is not None
    assert sql.execute("SELECT trigger, mode FROM run").fetchall() == [
        ("scheduled", "find_and_apply")
    ]


async def test_form_check_reads_one_form_per_ats(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    async with get_sessionmaker()() as session:
        targets = await form_check.targets(session)
    assert targets["lever"] is None and targets["ashby"] is None
    assert targets["greenhouse"] is not None

    calls: list[str] = []

    async def read(url: str, screenshot: Path | None = None) -> FormRead:
        calls.append(url)
        if len(calls) == 1:
            return FormRead(fields=list(FORM), captcha=False)
        raise FormError("No application form found on the page")

    monkeypatch.setattr(form_check, "read_form", read)
    async with get_sessionmaker()() as session:
        good = await form_check.check(session, "greenhouse", str(targets["greenhouse"]))
        bad = await form_check.check(session, "greenhouse", str(targets["greenhouse"]))
    assert good.ok and good.fields == len(FORM) and good.required == 4
    assert not bad.ok and len(calls) == 1 + form_check.TRIES  # one more try before failing
    ops = (await api.get("/api/ops")).json()
    assert [c["ats"] for c in ops["form_checks"]] == ["greenhouse"]
    assert ops["form_checks"][0]["ok"] is False  # the latest test
    async with get_sessionmaker()() as session:
        mail = await alerts.form_check_alert(session, [bad.id])
    assert mail is not None and "Greenhouse" in mail.subject
    assert (await api.post("/api/ops/form-check")).status_code == 202
    assert events[-1] == ("form-check/requested", {})


async def test_backup_files(db: str, tmp_path: Path) -> None:
    folder = backup.backup_dir()
    folder.mkdir(parents=True)
    now = datetime.now(IST)
    old = folder / f"jap-{(now - timedelta(days=20)):%Y%m%d-%H%M%S}.sql.gz"
    yesterday = folder / f"jap-{(now - timedelta(days=1)):%Y%m%d-%H%M%S}.sql.gz"
    for f in (old, yesterday):
        f.write_bytes(b"x")
    (folder / "notes.txt").write_text("not a backup")
    assert not backup.has_today()
    assert [b.path for b in backup.backups()] == [yesterday, old]
    assert backup.prune(now) == [old.name]
    if shutil.which("pg_dump") is None:
        pytest.skip("pg_dump is not installed here (the app image has it)")
    try:
        made = await backup.run_backup()
    except backup.BackupFailed as exc:
        pytest.skip(f"pg_dump here can't dump this server: {exc}")
    assert backup.has_today()
    with gzip.open(made.path) as dump:
        assert b"CREATE TABLE public.application" in dump.read()
    assert not list(folder.glob("*.part"))


async def test_ops_endpoint(
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]]
) -> None:
    ops = (await api.get("/api/ops")).json()
    assert ops == {"last_backup": None, "backups": 0, "backup_today": False, "form_checks": []}
    assert (await api.post("/api/ops/backup")).status_code == 202
    assert events[-1] == ("backup/requested", {})
    missing = await api.get(f"/api/ops/form-check/{uuid.uuid4()}/screenshot")
    assert missing.status_code == 404


async def test_submit_in_automatic_mode(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    """Automatic mode end to end on the database side: prepared, then submitted at once."""
    await api.patch("/api/profile", json={"apply_mode": "automatic"})
    _fake_form(monkeypatch, FORM)
    _known_mode(sql, setup["listing"])

    async def confirm(*args: Any) -> SubmitResult:
        return SubmitResult(confirmed=True, screenshot=False, clicked=True)

    monkeypatch.setattr(flow, "submit_form", confirm)
    app_id = await _prepare(setup["listing"])
    async with get_sessionmaker()() as session:
        assert await flow.submit(session, app_id) == {"status": "applied"}
    assert _status(sql, app_id) == ("applied", "applied")
