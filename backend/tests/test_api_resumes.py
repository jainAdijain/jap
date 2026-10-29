import json
from typing import Any

import httpx
import psycopg
import pytest

import llm.client as client
from functions.parse_resume import extract_step, parse_step
from tests.fixtures import RESUME, docx_bytes, pdf_bytes
from tests.test_llm_call import Stub

PDF = "application/pdf"


async def _upload(api: httpx.AsyncClient, name: str, data: bytes) -> httpx.Response:
    return await api.post("/api/resumes", files={"file": (name, data, "application/octet-stream")})


async def test_upload_creates_versions(api: httpx.AsyncClient, events: list[Any]) -> None:
    first = await _upload(api, "resume.pdf", pdf_bytes(RESUME))
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "parsing" and first.json()["is_active"]
    second = await _upload(api, "resume-v2.docx", docx_bytes(RESUME))
    assert second.status_code == 201
    versions = (await api.get("/api/resumes")).json()
    assert [v["original_name"] for v in versions] == ["resume-v2.docx", "resume.pdf"]
    assert [v["is_active"] for v in versions] == [True, False]
    assert [e[0] for e in events] == ["resume/uploaded", "resume/uploaded"]
    file = await api.get(f"/api/resumes/{versions[1]['id']}/file")
    assert file.status_code == 200 and file.content.startswith(b"%PDF")


async def test_rejects_other_files(api: httpx.AsyncClient) -> None:
    resp = await _upload(api, "resume.txt", b"hello")
    assert resp.status_code == 422
    resp = await _upload(api, "fake.pdf", b"PK not a pdf")
    assert resp.status_code == 422


async def test_parse_then_accept(
    api: httpx.AsyncClient,
    sql: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    events: list[tuple[str, dict[str, Any]]],
) -> None:
    sql.execute("INSERT INTO skill_alias VALUES ('postgresql', 'postgres'), ('k8s', 'kubernetes')")
    await api.put(
        "/api/profile",
        json={
            "address": "12 Lake Road, Indiranagar, Bengaluru",
            "must_have_skills": ["java"],
            "work_modes": ["remote"],
        },
    )
    version = (await _upload(api, "resume.pdf", pdf_bytes(RESUME))).json()

    early = await api.post(
        f"/api/resumes/{version['id']}/accept",
        json={"must_have_skills": [], "nice_to_have_skills": []},
    )
    assert early.status_code == 409

    reply = {
        "skills": ["Python", "PostgreSQL", "k8s", "[NAME]"],
        "titles": ["Senior Backend Engineer"],
        "years_experience": 9,
    }
    stub = Stub([json.dumps(reply)])
    monkeypatch.setattr(client, "chat", stub)
    await extract_step(version["id"])
    out = await parse_step(version["id"])
    # The redacted text that went to the model has no personal details (Stub asserts it).
    assert "[NAME]" in out["sent_to_model"] and "FastAPI" in out["sent_to_model"]

    parsed = (await api.get("/api/resumes")).json()[0]
    assert parsed["status"] == "parsed"
    assert parsed["suggested_skills"] == ["python", "postgres", "kubernetes"]
    assert "github.com/asharao" in parsed["links_found"]
    # Nothing replaced the profile's skills yet.
    assert (await api.get("/api/profile")).json()["must_have_skills"] == ["java"]

    accepted = await api.post(
        f"/api/resumes/{version['id']}/accept",
        json={"must_have_skills": ["python", "postgres"], "nice_to_have_skills": ["kubernetes"]},
    )
    assert accepted.status_code == 200 and accepted.json()["accepted_at"]
    profile = (await api.get("/api/profile")).json()
    assert profile["must_have_skills"] == ["python", "postgres"]
    assert profile["nice_to_have_skills"] == ["kubernetes"]
    # Accepting starts a run by itself (T1), after the resume upload's own event.
    [run] = sql.execute("SELECT id, trigger, status FROM run").fetchall()
    assert run[1:] == ("resume_updated", "queued")
    assert events[-1] == ("run/requested", {"run_id": str(run[0])})
