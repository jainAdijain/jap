import json
from typing import Any

import httpx
import psycopg
import pytest

import llm.client as client
from functions.fetch_board import fetch_step
from pipeline.enrich import direct_fields, embed_posting, extract_posting
from tests import boards as b
from tests.test_ingest import _board
from tests.test_llm_call import Stub

GH = "https://boards-api.greenhouse.io/v1/boards"
AS = "https://api.ashbyhq.com/posting-api/job-board"

FULL = {
    "skills": ["Python", "PostgreSQL", "sql"],
    "seniority": "lead",
    "work_mode": "remote",
    "salary_min": None,
    "salary_max": None,
    "salary_currency": None,
    "salary_period": None,
    "visa_sponsorship": "unknown",
    "remote_regions": ["United States"],
    "cover_letter_asked": False,
}


def _ids(sql: psycopg.Connection[Any]) -> list[Any]:
    return [r[0] for r in sql.execute("SELECT id FROM posting ORDER BY external_id")]


async def test_extract_fills_fields_once(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sql.execute("INSERT INTO skill_alias (alias, canonical) VALUES ('postgresql', 'postgres')")
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB)})
    await fetch_step(await _board())
    [posting_id] = _ids(sql)
    stub = Stub([json.dumps(FULL)])
    monkeypatch.setattr(client, "chat", stub)

    from database.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        result = await extract_posting(session, posting_id)
    assert result.skipped is False and "skills" in result.fields
    assert result.sent_to_model is not None and "<jd>" in result.sent_to_model
    extracted = sql.execute("SELECT extracted FROM posting").fetchone()[0]  # type: ignore[index]
    assert extracted["skills"] == ["python", "postgres", "sql"]
    assert extracted["work_mode"] == "remote" and extracted["direct"] == []
    assert "salary" not in extracted  # nothing stated: stays empty

    async with get_sessionmaker()() as session:
        assert (await extract_posting(session, posting_id)).skipped is True
    assert len(stub.bodies) == 1  # no second model call for an unchanged description


async def test_direct_fields_are_not_sent_for_extraction(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {f"{AS}/ramp": b.ashby(b.ASHBY_JOB)})
    await fetch_step(await _board("ashby", "ramp"))
    [posting_id] = _ids(sql)
    reply = {k: v for k, v in FULL.items() if not k.startswith("salary") and k != "work_mode"}
    stub = Stub([json.dumps(reply)])
    monkeypatch.setattr(client, "chat", stub)

    from database.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        result = await extract_posting(session, posting_id)
    assert "salary" not in result.fields and "work_mode" not in result.fields
    schema = stub.bodies[0]["response_format"]["json_schema"]["schema"]
    assert "salary_min" not in schema["properties"] and "work_mode" not in schema["properties"]
    extracted = sql.execute("SELECT extracted FROM posting").fetchone()[0]  # type: ignore[index]
    assert extracted["salary"]["min"] == 211400 and extracted["work_mode"] == "hybrid"
    assert extracted["direct"] == ["salary", "work_mode"]


def test_direct_fields() -> None:
    assert direct_fields("lever", {"workplaceType": "remote"}) == {"work_mode": "remote"}
    assert direct_fields("lever", {"workplaceType": "unspecified"}) == {}
    assert direct_fields("greenhouse", {"workplaceType": "remote"}) == {}


async def test_embed_is_cached_by_hash(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB)})
    await fetch_step(await _board())
    [posting_id] = _ids(sql)
    calls: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"embeddings": [[0.01] * 768]})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler))
    )
    from database.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        assert await embed_posting(session, posting_id) is True
        assert await embed_posting(session, posting_id) is False
    assert len(calls) == 1 and calls[0]["input"].startswith("search_document: ")
    assert sql.execute(
        "SELECT vector_dims(embedding), embedded_hash = description_hash FROM posting"
    ).fetchone() == (768, True)
