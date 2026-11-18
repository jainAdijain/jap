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
