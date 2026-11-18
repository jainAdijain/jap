"""The Claude reader (PRD 1.25, R1 to R7): with JAP_READER=claude nothing goes to OpenRouter,
the export carries job text only (redacted and guarded), and the import saves only what
passes the extraction schema, for the description that was exported."""

import json
from typing import Any

import psycopg
import pytest

import llm.client as client
from database.session import get_sessionmaker
from functions.fetch_board import fetch_step
from llm.redact import PersonalDataBlocked, Sensitive
from pipeline import claude_reader
from pipeline.dedup import dedup
from pipeline.enrich import extract_posting
from tests import boards as b
from tests.test_ingest import _board

GH = "https://boards-api.greenhouse.io/v1/boards"
ME = Sensitive(name="Asha Rao", emails=["asha.rao@example.com"], phones=["+91 98765 43210"])

# What the reader answers for a Greenhouse posting whose work mode the ATS states (JAP-10).
READ = {
    "skills": ["Python", "PostgreSQL"],
    "seniority": "lead",
    "salary_min": None,
    "salary_max": None,
    "salary_currency": None,
    "salary_period": None,
    "visa_sponsorship": "unknown",
    "cover_letter_asked": False,
}


def test_fields_are_checked_against_the_extraction_schema() -> None:
    wanted = ["skills", "seniority", "salary"]
    good = {"skills": ["go"], "seniority": "senior", "salary_min": 100, "salary_max": 150,
            "salary_currency": "USD", "salary_period": "year"}  # fmt: skip
    out = claude_reader.check_fields(wanted, good)
    assert out["salary"] == {"min": 100, "max": 150, "currency": "USD", "period": "year"}
    with pytest.raises(claude_reader.NotSaved, match="missing: salary_period"):
        claude_reader.check_fields(wanted, {k: v for k, v in good.items() if k != "salary_period"})
    with pytest.raises(claude_reader.NotSaved, match="not wanted: work_mode"):
        claude_reader.check_fields(wanted, {**good, "work_mode": "remote"})
    with pytest.raises(claude_reader.NotSaved, match="seniority"):
        claude_reader.check_fields(wanted, {**good, "seniority": "rockstar"})
    with pytest.raises(claude_reader.NotSaved, match="salary_period"):
        claude_reader.check_fields(wanted, {**good, "salary_period": "fortnight"})


def test_export_refuses_when_something_personal_is_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard runs on the job text: if redaction ever missed a value, nothing leaves."""
    posting = {"posting_id": "p1", "description_hash": "98765432109876543210",
               "keys": ["skills"], "title": "Engineer", "locations": ["India"],
               "description_text": "Questions? Write to asha.rao@example.com."}  # fmt: skip
    with pytest.raises(PersonalDataBlocked):
        claude_reader.build_export([posting], 1, False, ME)
    # Digits in ids and hashes are not mistaken for a phone number.
    posting["description_text"] = "Python and Postgres."
    out = claude_reader.build_export([posting], 1, False, ME)
    assert out["postings"] == [posting] and "Never guess" in out["instructions"]
    assert set(out["field_schema"]["properties"]) >= {"skills", "salary_min", "work_mode"}


async def test_unread_postings_go_through_export_and_import(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sql.execute("INSERT INTO skill_alias (alias, canonical) VALUES ('postgresql', 'postgres')")
    sql.execute(
        "INSERT INTO profile (full_name, email, phone) "
        "VALUES ('Asha Rao', 'asha.rao@example.com', '+91 98765 43210')"
    )
    content = b.GREENHOUSE_JOB["content"] + " Mail asha.rao@example.com"
    job = b.job(b.GREENHOUSE_JOB, content=content)
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(job)})
    await fetch_step(await _board())
    [posting_id] = [r[0] for r in sql.execute("SELECT id FROM posting")]

    async def no_openrouter(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("JAP_READER=claude must not call OpenRouter")

    monkeypatch.setattr(client, "chat", no_openrouter)
    async with get_sessionmaker()() as session:
        result = await extract_posting(session, posting_id)  # what enrich-posting runs
        await dedup(session)
    assert result.waiting_for_reader is True
    extracted, read_hash = sql.execute("SELECT extracted, extracted_hash FROM posting").fetchone()  # type: ignore[misc]
    assert extracted["work_mode"] == "remote" and read_hash is None  # ATS fields now, unread

    async with get_sessionmaker()() as session:
        out = await claude_reader.export(session)
    text = json.dumps(out)
    assert "Asha" not in text and "example.com" not in text and "98765" not in text
    assert out["unread_total"] == 1 and out["run_active"] is False
    [p] = out["postings"]
    assert p["posting_id"] == str(posting_id) and "Commercial Policy Lead" == p["title"]
    assert "work_mode" not in p["keys"] and "salary_min" in p["keys"]  # the ATS said remote

    async with get_sessionmaker()() as session:
        lines = await claude_reader.import_fields(
            session,
            {
                str(posting_id): {**READ, "description_hash": "stale"},
                "not-an-id": READ,
            },
        )
    assert lines[0].endswith("not saved (description changed since the export "
                             "(or no description_hash))")  # fmt: skip
    assert lines[1] == "not-an-id: not saved (not a posting id)" and lines[-1] == "saved 0 of 2"
    async with get_sessionmaker()() as session:
        lines = await claude_reader.import_fields(
            session, {str(posting_id): {**READ, "description_hash": p["description_hash"]}}
        )
    assert lines == [f"{posting_id}: saved", "saved 1 of 1"]

    extracted, read_hash, desc_hash = sql.execute(
        "SELECT extracted, extracted_hash, description_hash FROM posting"
    ).fetchone()  # type: ignore[misc]
    assert read_hash == desc_hash
    assert extracted["skills"] == ["python", "postgres"] and extracted["seniority"] == "lead"
    assert extracted["work_mode"] == "remote" and extracted["direct"] == [
        "remote_regions", "work_mode"]  # fmt: skip
    [skills] = sql.execute("SELECT skills FROM listing").fetchone()  # type: ignore[misc]
    assert skills == ["python", "postgres"]  # the listing was refreshed

    async with get_sessionmaker()() as session:
        assert (await claude_reader.export(session))["postings"] == []
        again = await claude_reader.import_fields(
            session, {str(posting_id): {**READ, "description_hash": p["description_hash"]}}
        )
    assert again[0].endswith("not saved (already read)")


async def test_export_comes_in_batches(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = [b.job(b.GREENHOUSE_JOB, id=8806482000 + i, offices=[]) for i in range(3)]
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(*jobs)})
    await fetch_step(await _board())
    async with get_sessionmaker()() as session:
        out = await claude_reader.export(session, limit=2)
    assert out["unread_total"] == 3 and len(out["postings"]) == 2
    assert "work_mode" in out["postings"][0]["keys"]  # the ATS said nothing this time
