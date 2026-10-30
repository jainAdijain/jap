"""Filtered listing (F1 to F3, U1, U2, U4, U10) on the demo jobs.

JAP Demo Co: Senior Backend (Bangalore + Remote India), Junior Backend (Bangalore), Senior
Frontend (Remote India), two Data Platform roles (Pune). Globex Demo: Backend (Remote -
Worldwide), Platform (Remote - US), SRE (Remote - APAC), Backend Intern (Bangalore), Python
Developer (London, work mode unclear), Data Analyst (Berlin).
"""

import re
from typing import Any

import httpx
import psycopg
import pytest

from database import demo
from pipeline.matching import WORLDWIDE, place_pattern, role_pattern, title_word_pattern

PROFILE: dict[str, Any] = {
    "target_roles": ["Backend Engineer"],
    "must_have_skills": ["python"],
    "locations": ["Bangalore"],
    "remote_regions": ["India"],
}
DEFAULT = {
    "Senior Backend Engineer - Bangalore", "Junior Backend Engineer", "Senior Frontend Engineer",
    "Backend Engineer", "Platform Engineer", "Backend Engineer Intern", "Python Developer",
}  # fmt: skip


async def _save(api: httpx.AsyncClient, **changes: Any) -> dict[str, Any]:
    resp = await api.put("/api/profile", json={**PROFILE, **changes})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _titles(api: httpx.AsyncClient, **params: str) -> set[str]:
    page = (await api.get("/api/listings", params=params)).json()
    assert page["total"] == len(page["rows"])
    return {r["title"] for r in page["rows"]}


@pytest.mark.parametrize(
    ("role", "title", "hit"),
    [
        ("Backend Engineer", "senior backend engineer", True),
        ("Backend Engineer", "backend developer", True),
        ("Backend Engineer", "backend engineering manager", False),
        ("Sr. Backend Engineer", "senior backend engineer", True),
        ("Platform Engineer", "data platform engineer", True),
        ("C++ Developer", "senior c++ developer", True),
        ("Engineer", "data analyst", False),
    ],
)
def test_role_pattern(role: str, title: str, hit: bool) -> None:
    pattern = role_pattern(role)
    assert pattern is not None
    assert bool(re.search(pattern, title)) is hit


def test_place_and_word_patterns() -> None:
    # A place typed with its region goes by the city: "Bangalore, India" still finds Bengaluru.
    typed = place_pattern("Bangalore, India")
    assert typed and re.search(typed, "bengaluru, karnataka, india")
    assert not re.search(typed, "pune, india")
    remote_india = place_pattern("Remote, India")
    assert remote_india and re.search(remote_india, "new delhi, india")
    bangalore = place_pattern("Bangalore")
    assert bangalore and re.search(bangalore, "bengaluru, karnataka, india")
    us = place_pattern("US")
    assert us and re.search(us, "remote - us") and not re.search(us, "austin")
    assert place_pattern("Remote") is None  # remote jobs go by Remote regions
    intern = title_word_pattern("Intern")
    assert intern and re.search(intern, "backend engineer intern")
    assert not re.search(intern, "internal tools engineer")
    assert re.search(WORLDWIDE, "remote - worldwide") and not re.search(WORLDWIDE, "remote - us")


async def test_default_rule_lists_any_condition_newest_first(api: httpx.AsyncClient) -> None:
    await demo.load()
    assert await _titles(api) == {"Backend Engineer"}  # empty profile: Remote - Worldwide only
    saved = await _save(api)
    assert saved["matching_count"] == len(DEFAULT)
    page = (await api.get("/api/listings")).json()
    assert {r["title"] for r in page["rows"]} == DEFAULT
    dates = [r["last_posted_at"] for r in page["rows"]]
    assert dates == sorted(dates, reverse=True)  # newest first
    rows = {r["title"]: r for r in page["rows"]}
    assert rows["Senior Backend Engineer - Bangalore"]["matched"] == ["role", "skills", "location"]
    assert rows["Senior Frontend Engineer"]["matched"] == ["location"]  # Remote, India region
    assert rows["Platform Engineer"]["matched"] == ["skills"]
    assert rows["Python Developer"]["work_mode"] is None  # unclear, still listed
    assert rows["Backend Engineer"]["matched_role"] == "Backend Engineer"
    assert rows["Platform Engineer"]["matched_role"] is None


async def test_rule_change_refilters_without_a_fetch(
    api: httpx.AsyncClient, events: list[tuple[str, dict[str, Any]]]
) -> None:
    await demo.load()
    await _save(api)
    rule = [["role", "location"]]
    preview = await api.post("/api/profile/preview", json={**PROFILE, "match_rule": rule})
    assert preview.json() == {
        "matching_count": 4,
        "by_condition": {"role": 4, "skills": 6, "location": 5},
    }
    assert len(await _titles(api)) == len(DEFAULT)  # preview saved nothing
    saved = await _save(api, match_rule=rule)
    assert saved["matching_count"] == 4
    assert await _titles(api) == {
        "Senior Backend Engineer - Bangalore", "Junior Backend Engineer",
        "Backend Engineer", "Backend Engineer Intern",
    }  # fmt: skip
    assert all(name != "run/requested" for name, _ in events)  # no fetch for a rule change
    rows = (await api.get("/api/listings")).json()["rows"]
    assert all({"role", "location"} <= set(r["matched"]) for r in rows)


async def test_remote_regions_and_worldwide(api: httpx.AsyncClient) -> None:
    await demo.load()
    await _save(api, match_rule=[["location"]])
    titles = await _titles(api)
    assert "Backend Engineer" in titles  # Remote - Worldwide always counts
    assert "Senior Frontend Engineer" in titles  # Remote, India allowed
    assert "Platform Engineer" not in titles  # Remote - US only
    assert "Site Reliability Engineer" not in titles  # Remote - APAC
    await _save(api, match_rule=[["location"]], remote_regions=["India", "APAC"])
    assert "Site Reliability Engineer" in await _titles(api)
    await _save(api, match_rule=[["location"]], remote_regions=["India", "USA"])
    assert "Platform Engineer" in await _titles(api)  # USA = US


async def test_all_remote_jobs(api: httpx.AsyncClient) -> None:
    """All remote jobs (remote_any): every remote job passes Location, whatever its regions."""
    await demo.load()
    await _save(api, match_rule=[["location"]], remote_regions=[], remote_any=True)
    titles = await _titles(api)
    assert {"Platform Engineer", "Site Reliability Engineer", "Backend Engineer"} <= titles
    saved = (await api.get("/api/profile")).json()
    assert saved["remote_any"] is True
    await _save(api, match_rule=[["location"]], remote_regions=[], remote_any=False)
    assert "Platform Engineer" not in await _titles(api)


async def test_exclusions_and_work_mode(api: httpx.AsyncClient) -> None:
    await demo.load()
    await _save(api, excluded_title_words=["intern"])
    assert "Backend Engineer Intern" not in await _titles(api)
    await _save(api, excluded_companies=["Globex Demo Inc."])  # cleaned like dedup
    titles = await _titles(api)
    assert titles == {
        "Senior Backend Engineer - Bangalore", "Junior Backend Engineer",
        "Senior Frontend Engineer",
    }  # fmt: skip
    await _save(api, work_modes=["remote"])
    titles = await _titles(api)
    assert "Junior Backend Engineer" not in titles  # onsite
    assert {"Python Developer", "Backend Engineer"} <= titles  # unclear and remote stay


async def test_filter_bar_and_summary_add_up(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    await demo.load()
    await _save(api)
    backend = await _titles(api, role="Backend Engineer")
    other = await _titles(api, role="__other")
    assert backend | other == DEFAULT and not backend & other
    assert await _titles(api, location="london") == {"Python Developer"}
    assert await _titles(api, matched="skills") == DEFAULT - {"Senior Frontend Engineer"}
    assert await _titles(api, status="applied") == set()

    summary = (await api.get("/api/listings/summary")).json()
    assert summary["totals"]["listings"] == len(DEFAULT)
    assert summary["totals"]["not_applied"] == len(DEFAULT)
    by_role = {r["role"]: r["listings"] for r in summary["by_role"]}
    assert by_role == {"Backend Engineer": len(backend), None: len(other)}
    assert sum(by_role.values()) == summary["totals"]["listings"]

    # Statuses count per listing; a closed listing leaves the totals and the default list.
    sql.execute("UPDATE listing SET status = 'skipped' WHERE title_display = 'Python Developer'")
    sql.execute("UPDATE listing SET status = 'closed' WHERE title_display = 'Platform Engineer'")
    summary = (await api.get("/api/listings/summary")).json()
    assert summary["totals"]["listings"] == len(DEFAULT) - 1
    assert summary["totals"]["skipped"] == 1
    assert await _titles(api, status="skipped") == {"Python Developer"}
    assert "Platform Engineer" in await _titles(api, status="closed")
    assert len(await _titles(api)) == summary["totals"]["listings"]


async def test_merge_keeps_matched_up_to_date(api: httpx.AsyncClient) -> None:
    await demo.load()
    await _save(api, match_rule=[["role"]])
    rows = {
        r["title"]: r
        for r in (await api.get("/api/listings", params={"q": "Backend"})).json()["rows"]
    }
    page = await api.get("/api/listings", params={"status": "all"})
    assert page.status_code == 200
    # Merging lists the listing under the kept one's conditions straight away.
    senior, junior = rows["Senior Backend Engineer - Bangalore"], rows["Junior Backend Engineer"]
    merged = await api.post(
        f"/api/listings/{senior['id']}/merge", json={"other_listing_id": junior["id"]}
    )
    assert merged.status_code == 200 and merged.json()["matched"] == ["role", "skills", "location"]


async def test_role_and_location_row_beside_the_default_rows_changes_nothing(
    api: httpx.AsyncClient,
) -> None:
    """Ticking Role and Location in the first default row keeps the Skills and Location rows,
    so the list stays the same; only the single row narrows it (the UI now says so)."""
    await demo.load()
    kept = await _save(api, match_rule=[["role", "location"], ["skills"], ["location"]])
    assert kept["matching_count"] == len(DEFAULT)
    only = await _save(api, match_rule=[["role", "location"]])
    assert only["matching_count"] == 4
    typed = await _save(api, match_rule=[["role", "location"]], locations=["Bangalore, India"])
    assert typed["matching_count"] == 4


async def test_list_sort_and_filters(api: httpx.AsyncClient) -> None:
    """Jobs table: sort by any column, Work mode and Posted filters, search over locations."""
    await demo.load()
    await _save(api)
    page = (await api.get("/api/listings", params={"sort": "title", "dir": "asc"})).json()
    titles = [r["title"] for r in page["rows"]]
    assert titles == sorted(titles)
    desc = (await api.get("/api/listings", params={"sort": "company", "dir": "desc"})).json()
    companies = [r["company"] for r in desc["rows"]]
    assert companies == sorted(companies, reverse=True)
    assert await _titles(api, work_mode="unknown") == {"Python Developer"}
    assert all(
        r["work_mode"] == "remote"
        for r in (await api.get("/api/listings", params={"work_mode": "remote"})).json()["rows"]
    )
    recent = (await api.get("/api/listings", params={"posted_days": 1})).json()["rows"]
    assert len(recent) < len(DEFAULT)
    assert await _titles(api, q="london") == {"Python Developer"}
    summary = (await api.get("/api/listings/summary")).json()
    assert summary["locations"] and all(isinstance(p, str) for p in summary["locations"])
