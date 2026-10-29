"""Cover letters (C1 to C6): only for a mandatory field, checked against the resume,
editable in Review mode, sent as text or a PDF and attached to the email.

The model is the OpenRouter stub from test_llm_call, which fails if it sees a personal
value; the PDF is built in real Chromium.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from pypdf import PdfReader

import llm.client as client
from database.session import get_sessionmaker
from pipeline import notify
from pipeline.apply import flow
from pipeline.apply.form import FormField, SubmitResult
from pipeline.cover_letter import CheckedClaim, problems
from tests.test_apply import FORM, RESUME_TEXT, _fake_form, _status
from tests.test_apply import setup as setup  # noqa: F401  (the fixture)
from tests.test_llm_call import Stub

LETTER_TEXT = FormField("cover", "Cover Letter", "textarea", True)
LETTER_FILE = FormField("cover_file", "Cover Letter", "file", True)
LETTER_OPTIONAL = FormField("cover", "Cover letter (optional)", "textarea", False)

GOOD = (
    "I am applying for the Backend Engineer role at Acme. Your team builds payment APIs in "
    "Python, and I built a payments API.\n\nI work in Python, FastAPI and Postgres every day "
    "and would like to bring that to Acme.\n\nBest regards,\n[NAME]"
)
CLAIMS_OK = {
    "claims": [
        {"claim": "built a payments API", "found": True, "evidence": "Built a payments API"},
        {"claim": "works in Python, FastAPI and Postgres", "found": True,
         "evidence": "Python, FastAPI, Postgres"},
    ]
}  # fmt: skip
UNTRUE = GOOD.replace("I built a payments API", "I led 12 engineers at Stripe")
CLAIMS_BAD = {
    "claims": [
        {"claim": "led 12 engineers at Stripe", "found": False, "evidence": ""},
        *CLAIMS_OK["claims"],
    ]
}


def _letter(text: str = GOOD) -> str:
    return json.dumps({"letter": text, "claims": [{"text": "x", "resume_evidence": "x"}]})


async def _prepare(listing_id: str) -> tuple[str, dict[str, Any] | None]:
    """create-attempt to map-fields, and cover-letter when map-fields asks for it."""
    async with get_sessionmaker()() as session:
        app_id = await flow.create_attempt(session, listing_id, None)
        url = await flow.snapshot(session, app_id)
        await flow.read_and_save(session, app_id, url)
        mapped = await flow.map_fields(session, app_id)
        written = None
        if mapped.status == "cover_letter":
            written = await flow.write_cover_letter(session, app_id)
    return app_id, written


async def test_mandatory_letter_is_written_checked_and_signed_locally(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    sql.execute("UPDATE profile SET tone_note = 'concise, no buzzwords'")
    _fake_form(monkeypatch, [*FORM, LETTER_TEXT])
    stub = Stub([_letter(), json.dumps(CLAIMS_OK)])
    monkeypatch.setattr(client, "chat", stub)
    app_id, written = await _prepare(setup["listing"])
    assert _status(sql, app_id) == ("ready_for_review", "ready_for_review")

    prompt = stub.bodies[0]["messages"][1]["content"]
    assert "Company: Acme" in prompt and "Role: Backend Engineer" in prompt
    assert "Build payment APIs" in prompt and "concise, no buzzwords" in prompt
    assert "[NAME]" in prompt and "Python, FastAPI, Postgres" in prompt
    assert len(stub.bodies) == 2  # the letter, then the claim check
    assert written is not None and written["letter"] == "written" and written["attempts"] == 1
    assert "[NAME]" in written["placeholders_in_prompt"] and written["name_filled_in_locally"]
    assert "asha" not in json.dumps(written).lower()  # the Inngest step output

    app = (await api.get(f"/api/applications/{app_id}")).json()
    letter = app["cover_letter"]
    assert letter["text"].endswith("Best regards,\nAsha Rao")  # [NAME] restored locally
    assert "Acme" in letter["text"] and "Backend Engineer" in letter["text"]
    assert letter["words"] < 250 and letter["source"] == "generated" and not letter["as_file"]
    assert [c["found"] for c in letter["claims"]] == [True, True]
    cover = next(f for f in app["fields"] if f["key"] == "cover")
    assert (cover["value"], cover["filled_from"]) == (letter["text"], "cover_letter")
    pdf = await api.get(f"/api/files/cover_letter/{app_id}")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


async def test_an_untrue_claim_regenerates_once(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _fake_form(monkeypatch, [*FORM, LETTER_TEXT])
    stub = Stub([_letter(UNTRUE), json.dumps(CLAIMS_BAD), _letter(), json.dumps(CLAIMS_OK)])
    monkeypatch.setattr(client, "chat", stub)
    app_id, written = await _prepare(setup["listing"])
    assert written is not None and written["attempts"] == 2
    fix = stub.bodies[2]["messages"][1]["content"]
    assert '"led 12 engineers at Stripe" is not in the resume' in fix
    assert "the number 12 is not in the resume" in fix
    assert _status(sql, app_id)[0] == "ready_for_review"
    text = sql.execute("SELECT cover_letter_text FROM application").fetchone()
    assert text is not None and "Stripe" not in text[0]


async def test_two_failures_go_to_needs_input_and_my_letter_is_used(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    _fake_form(monkeypatch, [*FORM, LETTER_FILE])
    bad = [_letter(UNTRUE), json.dumps(CLAIMS_BAD)]
    monkeypatch.setattr(client, "chat", Stub(bad + bad))
    app_id, written = await _prepare(setup["listing"])
    assert written is not None and written["letter"] == "failed twice"
    assert _status(sql, app_id) == ("needs_input", "needs_input")
    app = (await api.get(f"/api/applications/{app_id}")).json()
    assert [(q["key"], q["blocked"]) for q in app["pending"]] == [("cover_file", False)]
    assert "could not write a cover letter" in app["pending"][0]["reason"]
    assert app["cover_letter"] is None  # nothing is sent without the letter (C6)

    mine = "Dear Acme team,\n\nI would like to join as a Backend Engineer.\n\nAsha Rao"
    resp = await api.post(
        f"/api/applications/{app_id}/answer", json={"answers": {"cover_file": mine}}
    )
    assert resp.status_code == 200 and resp.json()["status"] == "preparing"
    assert (await api.get("/api/answers")).json() == []  # a letter is not an answer to bank
    monkeypatch.setattr(client, "chat", Stub([]))  # mine is used as it is
    async with get_sessionmaker()() as session:
        assert (await flow.map_fields(session, app_id)).status == "ready_for_review"
    app = (await api.get(f"/api/applications/{app_id}")).json()
    assert app["cover_letter"]["text"] == mine and app["cover_letter"]["source"] == "you"
    assert app["cover_letter"]["as_file"] and app["cover_letter"]["has_pdf"]
    field = next(f for f in app["fields"] if f["key"] == "cover_file")
    assert field["value"] == "Asha-Rao-Cover-Letter-Acme.pdf"


async def test_optional_letter_and_no_field_get_no_letter(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    sql.execute("UPDATE posting SET description_text = 'Please send a cover letter.'")
    _fake_form(monkeypatch, [*FORM, LETTER_OPTIONAL])
    monkeypatch.setattr(client, "chat", Stub([]))  # no model call at all
    app_id, written = await _prepare(setup["listing"])
    assert written is None
    assert _status(sql, app_id)[0] == "ready_for_review"
    app = (await api.get(f"/api/applications/{app_id}")).json()
    assert app["cover_letter"] is None
    cover = next(f for f in app["fields"] if f["key"] == "cover")
    assert (cover["value"], cover["filled_from"]) == ("", "")


async def test_edited_letter_is_what_is_sent_and_attached(
    setup: dict[str, Any], sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch,
    api: httpx.AsyncClient,
) -> None:  # fmt: skip
    _fake_form(monkeypatch, [*FORM, LETTER_FILE])
    monkeypatch.setattr(client, "chat", Stub([_letter(), json.dumps(CLAIMS_OK)]))
    app_id, _ = await _prepare(setup["listing"])
    edited = GOOD.replace("[NAME]", "Asha Rao").replace("every day", "every day (edited)")
    resp = await api.put(f"/api/applications/{app_id}", json={"cover_letter": edited})
    assert resp.status_code == 200
    assert resp.json()["cover_letter"]["source"] == "you"
    empty = await api.put(f"/api/applications/{app_id}", json={"cover_letter": "  "})
    assert empty.status_code == 422

    sent: dict[str, Any] = {}

    async def fake_submit(
        url: str, fields: list[FormField], answers: dict[str, str], files: dict[str, Any],
        shot: Path, trace: Path,
    ) -> SubmitResult:  # fmt: skip
        up = files["cover_file"]
        sent.update(name=up.name, mime=up.mime, pdf=up.path.read_bytes())
        return SubmitResult(confirmed=True, screenshot=False, clicked=True)

    monkeypatch.setattr(flow, "submit_form", fake_submit)
    assert (await api.post(f"/api/applications/{app_id}/approve")).status_code == 200
    async with get_sessionmaker()() as session:
        assert await flow.submit(session, app_id) == {"status": "applied"}
    assert sent["name"] == "Asha-Rao-Cover-Letter-Acme.pdf" and sent["mime"] == "application/pdf"
    pdf_path = setup["data"] / "sent.pdf"
    pdf_path.write_bytes(sent["pdf"])
    pdf_text = " ".join(PdfReader(pdf_path).pages[0].extract_text().split())
    assert "every day (edited)" in pdf_text and "Asha Rao" in pdf_text

    async with get_sessionmaker()() as session:
        mail = await notify.build(session, uuid.UUID(app_id))
    assert mail is not None and mail.attachment is not None
    assert mail.attachment.read_bytes() == sent["pdf"]  # the email attaches what was sent
    assert "Cover letter: attached" in mail.body
    app = (await api.get(f"/api/applications/{app_id}")).json()
    assert app["cover_letter_text"] == edited


def test_problems_checks_length_names_claims_and_numbers() -> None:
    ok = [CheckedClaim(claim="Python", found=True, evidence="Python, FastAPI")]
    resume = RESUME_TEXT
    assert problems(GOOD, ok, company="Acme", role="backend engineer", resume=resume) == []
    long = " ".join(["word"] * 250) + " Acme backend engineer"
    assert problems(long, ok, company="Acme", role="backend engineer", resume=resume) == [
        "it has 253 words; stay under 250"
    ]
    made_up = [CheckedClaim(claim="Kubernetes", found=True, evidence="Kubernetes at scale")]
    found = problems(
        "Acme backend engineer with 7 years", made_up, company="Acme", role="backend engineer",
        resume=resume,
    )  # fmt: skip
    assert found == [
        '"Kubernetes" is not in the resume',  # the quote is not really in the resume
        "the number 7 is not in the resume",
    ]
    assert problems("Hello", [], company="Acme", role="backend engineer", resume=resume) == [
        "name the company (Acme)",
        "name the role (backend engineer)",
    ]
