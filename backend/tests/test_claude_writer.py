"""The Claude writer's export and import (PRD 1.22, W2 to W6): nothing personal leaves in
the export, and answers come back with the real values put in locally."""

import json
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

import llm.client as client
from database.session import get_sessionmaker
from llm.redact import PersonalDataBlocked, Sensitive
from pipeline import claude_writer
from tests.test_apply import FORM, NOTICE, WHY, _fake_form, _prepare, _status

ME = Sensitive(
    name="Asha Rao",
    emails=["asha.rao@example.com"],
    phones=["+91 98765 43210"],
    links=["https://github.com/asharao", "https://linkedin.com/in/asharao"],
)


def test_export_swaps_every_personal_value_and_guards() -> None:
    out = claude_writer.build_export(
        [{"application_id": "a1", "company": "Acme", "title": "Backend Engineer",
          "job_description": "Write to asha.rao@example.com.",
          "questions": [{"key": "why", "label": "Why Acme?", "reason": "draft failed"}]}],
        "Asha Rao\n+91 98765 43210 | asha.rao@example.com | github.com/asharao\nPython.",
        [("GitHub", "https://github.com/asharao")],
        ME,
    )  # fmt: skip
    text = json.dumps(out)
    for secret in ("Asha", "Rao", "example.com", "98765", "asharao"):
        assert secret not in text
    assert out["resume"].startswith("[NAME]") and "Python." in out["resume"]
    assert out["answer_bank"][0]["answer"] == "[LINK_1]"
    assert out["jobs"][0]["questions"][0]["why_open"] == "draft failed"


def test_export_refuses_when_something_personal_is_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard runs on the whole export: if redaction ever missed a value, nothing leaves."""
    monkeypatch.setattr(claude_writer, "_clean", lambda text, values: text)
    with pytest.raises(PersonalDataBlocked):
        claude_writer.build_export([], "Asha Rao, Python.", [], ME)


def test_answers_come_back_with_real_values() -> None:
    back = claude_writer.restore_answers(
        {"why": "I'm [NAME]; see [LINK_1].", "x": "ping [EMAIL] [LINK]"}, ME
    )
    assert back == {"why": "I'm Asha Rao; see https://github.com/asharao.",
                    "x": "ping asha.rao@example.com"}  # fmt: skip


async def test_export_and_import_through_needs_input(
    db: str, sql: psycopg.Connection[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    from tests.test_apply import _listing, _profile, _resume

    _profile(sql)
    _resume(sql, tmp_path)
    listing = _listing(sql)
    _fake_form(monkeypatch, [*FORM, WHY, NOTICE])
    monkeypatch.setattr(client, "chat", _no_credit)  # out of credit: nothing drafted
    app_id = await _prepare(listing)
    assert _status(sql, app_id)[0] == "needs_input"

    sql.execute(
        "INSERT INTO answer_bank (question_pattern, question_norm, answer) VALUES"
        " ('GitHub', 'github', 'asharao-dev'), ('Phone', 'phone', '98765 43210'),"
        " ('Do you need a visa?', 'do you need a visa', 'No')"
    )
    async with get_sessionmaker()() as session:
        out = await claude_writer.export(session)
    text = json.dumps(out)
    assert "Asha" not in text and "example.com" not in text and "asharao" not in text
    assert out["answer_bank"] == [{"question": "Do you need a visa?", "answer": "No"}]
    job = out["jobs"][0]
    assert job["application_id"] == app_id
    assert [q["key"] for q in job["questions"]] == ["why", "notice"]
    assert job["questions"][0]["max_chars"] == 600

    async with get_sessionmaker()() as session:
        lines = await claude_writer.import_answers(
            session, {app_id: {"why": "I'm [NAME] and I build payment APIs.", "notice": "30 days"}}
        )
    assert lines == [f"{app_id}: preparing"]
    assert events[-1] == ("application/answered", {"application_id": app_id})
    row = sql.execute("SELECT answers FROM application WHERE id = %s", (app_id,)).fetchone()
    assert row is not None and row[0]["why"] == "I'm Asha Rao and I build payment APIs."
    assert sql.execute("SELECT count(*) FROM answer_bank").fetchone() == (3,)  # drafts not kept

    async with get_sessionmaker()() as session:  # not waiting any more: refused, nothing changed
        again = await claude_writer.import_answers(session, {app_id: {"why": "x", "notice": "y"}})
    assert again[0].startswith(f"{app_id}: not answered")


async def _no_credit(*args: Any, **kwargs: Any) -> Any:
    raise client.OutOfCredits("OpenRouter 402: out of credits")


async def test_the_claude_writer_writes_the_required_cover_letter(
    db: str, sql: psycopg.Connection[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    events: list[tuple[str, dict[str, Any]]],
) -> None:  # fmt: skip
    """JAP_WRITER=claude (PRD 1.28 W11): no model writes the letter; it waits at Needs input,
    goes out marked cover_letter with JAP's letter rules, and comes back as the letter."""
    from pipeline.apply import flow
    from tests.test_apply import _listing, _profile, _resume
    from tests.test_cover_letter import LETTER_TEXT
    from tests.test_llm_call import Stub

    _profile(sql)
    _resume(sql, tmp_path)
    listing = _listing(sql)
    stub = Stub([])  # any model call would fail the test
    monkeypatch.setattr(client, "chat", stub)
    _fake_form(monkeypatch, [*FORM, LETTER_TEXT])
    async with get_sessionmaker()() as session:
        app_id = await flow.create_attempt(session, listing, None)
        url = await flow.snapshot(session, app_id)
        await flow.read_and_save(session, app_id, url)
        assert (await flow.map_fields(session, app_id)).status == "cover_letter"
        written = await flow.write_cover_letter(session, app_id)
    assert written["letter"] == "waiting for the Claude writer" and stub.bodies == []
    assert _status(sql, app_id)[0] == "needs_input"

    async with get_sessionmaker()() as session:
        out = await claude_writer.export(session)
    [q] = out["jobs"][0]["questions"]
    assert q["key"] == "cover" and q["cover_letter"] is True
    assert q["why_open"] == "Waiting for the Claude writer"
    assert "Use only facts in <resume>" in out["cover_letter_rules"]
    assert "Return JSON" not in out["cover_letter_rules"]

    async def fake_pdf(path: Path, text: str, **kwargs: Any) -> Path:
        return path  # the PDF itself is tested in test_cover_letter.py

    monkeypatch.setattr(flow, "write_pdf", fake_pdf)
    letter = "Acme's payment work fits what I build.\n\nRegards,\n[NAME]"
    async with get_sessionmaker()() as session:
        lines = await claude_writer.import_answers(session, {app_id: {"cover": letter}})
    assert lines == [f"{app_id}: preparing"]
    row = sql.execute(
        "SELECT cover_letter_text, cover_letter_path FROM application WHERE id = %s", (app_id,)
    ).fetchone()
    assert row is not None and row[0].endswith("Regards,\nAsha Rao") and row[1]


async def test_the_claude_writer_reads_a_new_resume(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """JAP_WRITER=claude (PRD 1.28 W12): no model reads the resume; it shows Reading until
    the writer's skills come back through writer-import, then the suggestions wait for me."""
    from functions.parse_resume import extract_step, parse_step
    from tests.fixtures import RESUME, pdf_bytes
    from tests.test_llm_call import Stub

    sql.execute("INSERT INTO skill_alias VALUES ('postgresql', 'postgres')")
    await api.put("/api/profile", json={"full_name": "Asha Rao", "email": "asha.rao@example.com"})
    stub = Stub([])
    monkeypatch.setattr(client, "chat", stub)
    files = {"file": ("resume.pdf", pdf_bytes(RESUME), "application/octet-stream")}
    version = (await api.post("/api/resumes", files=files)).json()
    await extract_step(version["id"])
    assert (await parse_step(version["id"]))["waiting_for_writer"] is True
    assert stub.bodies == []
    assert (await api.get("/api/resumes")).json()[0]["status"] == "parsing"

    async with get_sessionmaker()() as session:
        out = await claude_writer.export(session)
    [resume] = out["resumes"]
    assert resume["resume_id"] == version["id"] and "FastAPI" in resume["text"]
    assert "Asha" not in json.dumps(out) and "years_experience" in out["resume_rules"]
    key = f"resume:{version['id']}"
    reply = {"skills": ["Python", "PostgreSQL"], "titles": ["Backend Engineer"],
             "years_experience": 9}  # fmt: skip
    async with get_sessionmaker()() as session:
        lines = await claude_writer.import_answers(session, {key: reply})
        again = await claude_writer.import_answers(session, {key: reply})
        assert (await claude_writer.export(session))["resumes"] == []
    assert lines == [f"{key}: read (2 skills)"]
    assert again == [f"{key}: not read (already read, or gone)"]
    parsed = (await api.get("/api/resumes")).json()[0]
    assert parsed["status"] == "parsed" and parsed["suggested_skills"] == ["python", "postgres"]
