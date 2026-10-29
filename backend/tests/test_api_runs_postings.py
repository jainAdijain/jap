from typing import Any

import httpx
import psycopg
import pytest

from functions.fetch_board import fetch_step
from functions.run_pipeline import summarize
from tests import boards as b
from tests.test_ingest import _board

GH = "https://boards-api.greenhouse.io/v1/boards"


async def test_find_only_run_is_queued_once(
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]]
) -> None:
    first = await api.post("/api/runs", json={"mode": "find_only"})
    assert first.status_code == 202 and first.json()["status"] == "queued"
    second = await api.post("/api/runs", json={})
    assert second.json()["id"] == first.json()["id"]  # repeats collapse (T4)
    assert events == [("run/requested", {"run_id": first.json()["id"]})]
    latest = await api.get("/api/runs/latest")
    assert latest.json()["id"] == first.json()["id"]
    bad = await api.post("/api/runs", json={"mode": "find_everything"})
    assert bad.status_code == 422


async def test_no_run_yet(api: httpx.AsyncClient) -> None:
    resp = await api.get("/api/runs/latest")
    assert resp.status_code == 200 and resp.json() is None


async def test_detect_endpoint(api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    b.fake_boards(
        monkeypatch,
        {
            f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB),
            f"{GH}/discord": {"name": "Discord", "content": ""},
        },
    )
    ok = await api.post("/api/boards/detect", json={"text": "https://boards.greenhouse.io/discord"})
    assert ok.status_code == 200
    assert ok.json() == {
        "ats": "greenhouse", "board_token": "discord", "display_name": "Discord",
        "job_count": 1, "already_watched": False,
    }  # fmt: skip
    bad = await api.post("/api/boards/detect", json={"text": "https://example.com/careers"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "not_a_board"
    missing = await api.post(
        "/api/boards/detect", json={"text": "https://boards.greenhouse.io/madeup"}
    )
    assert (
        missing.status_code == 422
        and "no board called madeup" in missing.json()["error"]["message"]
    )
    assert (await api.get("/api/boards")).json() == []  # nothing saved by detect


async def test_job_list_and_side_panel(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    second = b.job(b.GREENHOUSE_JOB, id=1002, title="Policy Analyst",
                   first_published="2026-10-01T00:00:00Z")  # fmt: skip
    b.fake_boards(monkeypatch, {f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB, second)})
    await fetch_step(await _board())
    page = (await api.get("/api/postings")).json()
    assert page["total"] == 2
    assert [r["title"] for r in page["rows"]] == ["Policy Analyst", "Commercial Policy Lead"]
    assert page["rows"][0]["extracted"] is False
    found = (await api.get("/api/postings", params={"q": "lead"})).json()
    assert [r["title"] for r in found["rows"]] == ["Commercial Policy Lead"]

    pid = page["rows"][1]["id"]
    sql.execute(
        'UPDATE posting SET extracted = \'{"skills": ["python"], "work_mode": "remote", '
        '"direct": []}\', extracted_hash = description_hash WHERE id = %s',
        (pid,),
    )
    detail = (await api.get(f"/api/postings/{pid}")).json()
    assert detail["extracted"] is True and detail["fields"]["skills"] == ["python"]
    assert detail["work_mode"] == "remote" and "Own commercial policy" in detail["description_text"]
    assert (await api.get("/api/runs/latest")).json() is None

    # Stop watching the company: its jobs leave the list.
    board_id = (await api.get("/api/boards")).json()[0]["id"]
    await api.delete(f"/api/boards/{board_id}")
    assert (await api.get("/api/postings")).json()["total"] == 0


def test_summarize_counts_and_errors() -> None:
    counts = summarize(
        [
            {"ok": True, "fetched": 3, "new": 2, "unchanged": 1, "closed": 1, "enrich_queued": 2},
            {"ok": False, "error": "Gone: board not found on lever"},
        ]
    )
    assert counts["boards"] == 2 and counts["boards_failed"] == 1
    assert counts["new"] == 2 and counts["closed"] == 1 and counts["enrich_queued"] == 2
    assert counts["board_errors"] == ["Gone: board not found on lever"]
