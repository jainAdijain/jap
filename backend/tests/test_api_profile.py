from typing import Any

import httpx
import psycopg


async def test_empty_profile_on_first_read(api: httpx.AsyncClient) -> None:
    body = (await api.get("/api/profile")).json()
    assert body["full_name"] is None and body["target_roles"] == []
    assert body["work_modes"] == ["remote", "hybrid", "onsite"]
    assert body["match_rule"] == [["role"], ["skills"], ["location"]]
    assert body["apply_mode"] == "review"
    assert body["spend"] == {"month_usd": 0.0, "cap_usd": 2.0, "reached": False}


PROFILE = {
    "full_name": "Asha Rao",
    "email": "asha.rao@example.com",
    "phone": "+91 98765 43210",
    "links": [{"label": "GitHub", "url": "https://github.com/asharao"}],
    "target_roles": ["Backend Engineer", " backend  engineer ", "Data Engineer"],
    "must_have_skills": ["Python", "postgresql"],
    "nice_to_have_skills": ["k8s", "python"],
    "locations": ["Bengaluru", "Remote"],
    "remote_regions": ["India", "APAC"],
    "match_rule": [["role", "location"], ["skills"]],
    "work_modes": ["remote", "hybrid"],
    "excluded_companies": ["Initech"],
    "excluded_title_words": ["Intern"],
    "tone_note": "concise, no buzzwords",
}


async def test_save_and_reload(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], events: list[Any]
) -> None:
    sql.execute("INSERT INTO skill_alias VALUES ('postgresql', 'postgres'), ('k8s', 'kubernetes')")
    saved = await api.put("/api/profile", json=PROFILE)
    assert saved.status_code == 200, saved.text
    assert saved.json()["matching_count"] == 0
    body = (await api.get("/api/profile")).json()
    assert body["target_roles"] == ["Backend Engineer", "Data Engineer"]
    assert body["must_have_skills"] == ["python", "postgres"]
    assert body["nice_to_have_skills"] == ["kubernetes"]  # python is already must-have
    assert body["match_rule"] == [["location", "role"], ["skills"]]
    assert body["links"] == [{"label": "GitHub", "url": "https://github.com/asharao"}]
    assert events == [("profile/saved", {})]


async def test_work_mode_is_required(api: httpx.AsyncClient) -> None:
    resp = await api.put("/api/profile", json={**PROFILE, "work_modes": []})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation"
    assert "at least one work mode" in resp.json()["error"]["message"]


async def test_bad_email_is_rejected(api: httpx.AsyncClient) -> None:
    resp = await api.put("/api/profile", json={**PROFILE, "email": "not-an-email"})
    assert resp.status_code == 422


async def test_switches(api: httpx.AsyncClient) -> None:
    body = (await api.patch("/api/profile", json={"apply_mode": "automatic"})).json()
    assert body["apply_mode"] == "automatic" and body["daily_run"] is False
    body = (await api.patch("/api/profile", json={"daily_run": True})).json()
    assert body["apply_mode"] == "automatic" and body["daily_run"] is True
    assert body["daily_run_at"] == "06:00:00"
    body = (await api.patch("/api/profile", json={"daily_run_at": "16:42:31"})).json()
    assert body["daily_run_at"] == "16:42:00" and body["daily_run"] is True
    bad = await api.patch("/api/profile", json={"daily_run_at": "25:00"})
    assert bad.status_code == 422


async def test_spend_shows_this_month(api: httpx.AsyncClient, sql: psycopg.Connection[Any]) -> None:
    sql.execute(
        "INSERT INTO llm_call (purpose, model, prompt_version, input_sha256, cost_usd) "
        "VALUES ('extract', 'm', 'v1', repeat('a', 64), 0.0125),"
        " ('extract', 'm', 'v1', repeat('b', 64), 0.5)"
    )
    sql.execute("UPDATE llm_call SET created_at = now() - interval '40 days' WHERE cost_usd = 0.5")
    body = (await api.get("/api/profile")).json()
    assert body["spend"]["month_usd"] == 0.0125
