"""Filtered listing (F1 to F6, U1, U2, U4, U10) on the demo jobs.

JAP Demo Co: Senior Backend (Bangalore + Remote India), Junior Backend (Bangalore), Senior
Frontend (Remote India), two Data Platform roles (Pune). Globex Demo: Backend (Remote -
Worldwide), Platform (Remote - US), SRE (Remote - APAC), Backend Intern (Bangalore), Python
Developer (London, work mode unclear), Data Analyst (Berlin).

Skills never hide a job (PRD 1.21): the match rule is job title and location only, and each
listing carries a skills match instead (the tests from test_skills_never_hide_a_job down).
"""

import re
from typing import Any

import httpx
import psycopg
import pytest

from database import demo
from database.session import get_sessionmaker
from pipeline.matching import (
    DEFAULT_RULE,
    WORLDWIDE,
    Criteria,
    clean_rule,
    place_pattern,
    refilter_from_profile,
    role_pattern,
    title_word_pattern,
)

PROFILE: dict[str, Any] = {
    "target_roles": ["Backend Engineer", "Python Developer"],
    "must_have_skills": ["python"],
    "locations": ["Bangalore"],
    "remote_regions": ["India", "US"],
    "match_rule": [["role"], ["location"]],  # the broad choice: job title or location
}
# Fits on job title and location, the default rule.
BOTH = {
    "Senior Backend Engineer - Bangalore", "Junior Backend Engineer",
    "Backend Engineer", "Backend Engineer Intern",
}  # fmt: skip
# What each demo job asks for, as the demo loads it.
SKILLS: dict[str, Any] = {
    "must_have_skills": ["python", "postgresql", "rust"],
    "nice_to_have_skills": ["docker", "kafka"],
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


async def test_title_or_location_lists_newest_first(api: httpx.AsyncClient) -> None:
    await demo.load()
    assert await _titles(api) == set()  # empty profile: no job title to fit, nothing listed
    saved = await _save(api)
    assert saved["matching_count"] == len(DEFAULT)
    page = (await api.get("/api/listings")).json()
    assert {r["title"] for r in page["rows"]} == DEFAULT
    dates = [r["last_posted_at"] for r in page["rows"]]
    assert dates == sorted(dates, reverse=True)  # newest first
    rows = {r["title"]: r for r in page["rows"]}
    assert rows["Senior Backend Engineer - Bangalore"]["matched"] == ["role", "location"]
    assert rows["Senior Frontend Engineer"]["matched"] == ["location"]  # Remote, India region
    assert rows["Platform Engineer"]["matched"] == ["location"]  # Remote, US region
    assert rows["Python Developer"]["matched"] == ["role"]
    assert all("skills" not in r["matched"] for r in page["rows"])  # never a condition
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
        "by_condition": {"role": 5, "location": 6},
    }
    assert len(await _titles(api)) == len(DEFAULT)  # preview saved nothing
    saved = await _save(api, match_rule=rule)
    assert saved["matching_count"] == 4
    assert await _titles(api) == BOTH
    assert all(name != "run/requested" for name, _ in events)  # no fetch for a rule change
    rows = (await api.get("/api/listings")).json()["rows"]
    assert all({"role", "location"} <= set(r["matched"]) for r in rows)


async def test_remote_regions_and_worldwide(api: httpx.AsyncClient) -> None:
    await demo.load()
    await _save(api, match_rule=[["location"]], remote_regions=["India"])
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
    python = await _titles(api, role="Python Developer")
    other = await _titles(api, role="__other")
    assert backend | python | other == DEFAULT and not backend & other
    assert python == {"Python Developer"}
    assert await _titles(api, location="london") == {"Python Developer"}
    assert await _titles(api, matched="location") == DEFAULT - {"Python Developer"}
    assert await _titles(api, matched="role") == backend | python
    # Skills are not a condition a job can fit on any more (F5).
    assert (await api.get("/api/listings", params={"matched": "skills"})).status_code == 422
    assert await _titles(api, status="applied") == set()

    summary = (await api.get("/api/listings/summary")).json()
    assert summary["totals"]["listings"] == len(DEFAULT)
    assert summary["totals"]["not_applied"] == len(DEFAULT)
    by_role = {r["role"]: r["listings"] for r in summary["by_role"]}
    assert by_role == {"Backend Engineer": len(backend), "Python Developer": 1, None: len(other)}
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
    assert merged.status_code == 200 and merged.json()["matched"] == ["role", "location"]


async def test_rule_saved_with_skills_keeps_its_other_conditions(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """A match rule from before 1.21 that used skills keeps its other conditions; a
    combination of skills alone is dropped, and a rule left empty becomes title and location."""
    await demo.load()
    kept = await _save(api, match_rule=[["role", "location"], ["skills"], ["location"]])
    assert kept["match_rule"] == [["location", "role"], ["location"]]
    assert kept["matching_count"] == len(DEFAULT) - 1  # every job that fits on location
    third = await _save(api, match_rule=[["location", "role", "skills"]])  # the old 3rd choice
    assert third["match_rule"] == [["location", "role"]] and third["matching_count"] == 4
    assert await _titles(api) == BOTH  # no job dropped for the skills it names
    alone = await _save(api, match_rule=[["skills"]])
    assert alone["match_rule"] == [["location", "role"]] and alone["matching_count"] == 4
    typed = await _save(api, match_rule=[["role", "location"]], locations=["Bangalore, India"])
    assert typed["matching_count"] == 4
    empty = await api.put("/api/profile", json={**PROFILE, "match_rule": []})
    assert empty.status_code == 422  # an empty rule typed now is still refused

    # A rule still stored with skills (a row the migration has not seen) is read the same way.
    sql.execute("""UPDATE profile SET match_rule = '[["skills"], ["role", "skills"]]'""")
    assert (await api.get("/api/profile")).json()["match_rule"] == [["role"]]
    run = await api.post("/api/profile/preview", json={**PROFILE, "match_rule": [["role"]]})
    assert run.json()["matching_count"] == 5


def test_clean_rule() -> None:
    assert clean_rule([["role", "skills"], ["skills"]]) == (("role",),)
    assert clean_rule([["skills"]]) == DEFAULT_RULE == (("role", "location"),)
    assert clean_rule(None) == DEFAULT_RULE and clean_rule([]) == DEFAULT_RULE
    assert clean_rule([["location", "role"], ["role", "location"]]) == (("role", "location"),)
    assert Criteria().match_rule == DEFAULT_RULE  # no profile: title and location, not any-of


async def _rows(api: httpx.AsyncClient, **params: Any) -> list[dict[str, Any]]:
    page = (await api.get("/api/listings", params=params)).json()
    rows: list[dict[str, Any]] = page["rows"]
    return rows


async def test_skills_never_hide_a_job(api: httpx.AsyncClient) -> None:
    """F5: the list is the same whatever my skills are; each job shows its skills match."""
    await demo.load()
    before = (await _save(api, must_have_skills=[], nice_to_have_skills=[]))["matching_count"]
    saved = await _save(api, **SKILLS)
    assert saved["matching_count"] == before == len(DEFAULT)
    assert await _titles(api) == DEFAULT
    match = {r["title"]: r["skills_match"] for r in await _rows(api)}
    assert match["Backend Engineer"] == {  # asks for python, postgresql, docker
        "stated": True,
        "required_hit": ["python", "postgresql"],
        "required_missing": ["rust"],
        "required_total": 3,
        "required_pct": 67,
        "nice_hit": ["docker"],
        "nice_total": 2,
    }
    intern = match["Backend Engineer Intern"]  # python only
    assert intern["required_pct"] == 33 and intern["required_missing"] == ["postgresql", "rust"]
    # A job that names none of my required skills is listed all the same, at 0%.
    frontend = match["Senior Frontend Engineer"]  # kafka, spark
    assert frontend["required_pct"] == 0 and frontend["required_hit"] == []
    assert frontend["required_missing"] == SKILLS["must_have_skills"]
    assert frontend["nice_hit"] == ["kafka"]
    # The strictest old setting (every required skill) would have listed nothing.
    assert all(m["required_pct"] < 100 for m in match.values())
    # The panel carries the same match.
    row = next(r for r in await _rows(api) if r["title"] == "Backend Engineer")
    detail = (await api.get(f"/api/listings/{row['id']}")).json()
    assert detail["skills_match"] == match["Backend Engineer"]
    assert detail["nice_skills"] == ["docker"]


async def test_skills_not_stated_is_unknown_not_a_fail(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """A job with no extracted skills is listed and reads Skills not stated, never 0%."""
    await demo.load()
    sql.execute("UPDATE listing SET skills = '{}' WHERE title_display = 'Python Developer'")
    await _save(api, **SKILLS)
    rows = {r["title"]: r for r in await _rows(api)}
    assert set(rows) == DEFAULT
    assert rows["Python Developer"]["skills_match"] == {
        "stated": False,
        "required_hit": [],
        "required_missing": [],
        "required_total": 3,
        "required_pct": None,
        "nice_hit": [],
        "nice_total": 2,
    }
    # A minimum leaves unknown jobs out; no minimum (or 0) keeps them.
    assert "Python Developer" not in await _titles(api, min_skills="1")
    assert "Python Developer" in await _titles(api, min_skills="0")


async def test_no_required_skills_shows_only_nice_to_have(api: httpx.AsyncClient) -> None:
    await demo.load()
    await _save(api, must_have_skills=[], nice_to_have_skills=["docker", "kafka"])
    match = {r["title"]: r["skills_match"] for r in await _rows(api)}
    assert set(match) == DEFAULT
    for m in match.values():
        assert m["required_total"] == 0 and m["required_pct"] is None
        assert m["required_hit"] == [] and m["required_missing"] == []
    assert match["Backend Engineer"]["nice_hit"] == ["docker"]
    assert match["Backend Engineer"]["nice_total"] == 2
    assert await _titles(api, min_skills="50") == set()  # nothing to measure against


async def test_sort_and_filter_by_skills_match(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """Jobs sorts by skills match (required %, then nice to have) and filters by a minimum,
    without changing Job preferences."""
    await demo.load()
    sql.execute("UPDATE listing SET skills = '{}' WHERE title_display = 'Python Developer'")
    await _save(api, **SKILLS)
    profile = (await api.get("/api/profile")).json()
    down = await _rows(api, sort="skills", dir="desc")
    assert {r["title"] for r in down} == DEFAULT
    pcts = [r["skills_match"]["required_pct"] for r in down]
    assert pcts[-1] is None and None not in pcts[:-1]  # unknown last
    known = pcts[:-1]
    assert known == sorted(known, reverse=True) and known[0] == 67 and known[-1] == 0
    # Equal required %: more nice-to-have first (Backend Engineer has docker, the others none).
    assert down[0]["title"] == "Backend Engineer"
    up = await _rows(api, sort="skills", dir="asc")
    pcts = [r["skills_match"]["required_pct"] for r in up]
    assert pcts[-1] is None and pcts[:-1] == sorted(pcts[:-1])  # unknown last here too

    half = await _rows(api, min_skills="50")
    assert half and all(r["skills_match"]["required_pct"] >= 50 for r in half)
    assert {r["title"] for r in half} == {
        "Senior Backend Engineer - Bangalore", "Junior Backend Engineer", "Backend Engineer",
    }  # fmt: skip
    third = await _titles(api, min_skills="33")
    assert third == {r["title"] for r in half} | {"Backend Engineer Intern", "Platform Engineer"}
    assert await _titles(api, min_skills="100") == set()
    assert (await api.get("/api/listings", params={"min_skills": 101})).status_code == 422
    # The filter narrowed the view only: the profile and the list itself are as they were.
    assert (await api.get("/api/profile")).json() == profile
    assert await _titles(api) == DEFAULT


async def test_skills_match_follows_my_skills_and_the_job(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    """Saving my skills rewrites every job's match at once (F1), and a job description read
    later updates that job's match (the refilter after enrichment)."""
    await demo.load()
    await _save(api, **SKILLS)
    first = {r["title"]: r["skills_match"]["required_pct"] for r in await _rows(api)}
    assert first["Backend Engineer"] == 67
    await _save(api, must_have_skills=["python"], nice_to_have_skills=["postgresql"])
    match = {r["title"]: r["skills_match"] for r in await _rows(api)}
    assert match["Backend Engineer"]["required_pct"] == 100
    assert match["Backend Engineer"]["nice_hit"] == ["postgresql"]
    assert match["Senior Frontend Engineer"]["required_pct"] == 0
    # A skill in both lists counts as required only.
    await _save(api, must_have_skills=["python"], nice_to_have_skills=["python", "docker"])
    both = {r["title"]: r["skills_match"] for r in await _rows(api)}
    assert both["Backend Engineer"]["nice_total"] == 1
    assert both["Backend Engineer"]["nice_hit"] == ["docker"]

    row = sql.execute(
        "UPDATE listing SET skills = '{rust,go}' WHERE title_display = 'Backend Engineer'"
        " RETURNING id"
    ).fetchone()
    assert row is not None
    async with get_sessionmaker()() as session:
        await refilter_from_profile(session, [row[0]])
        await session.commit()
    again = {r["title"]: r["skills_match"] for r in await _rows(api)}
    assert again["Backend Engineer"]["required_pct"] == 0
    assert again["Backend Engineer"]["stated"] is True
    assert again["Senior Backend Engineer - Bangalore"]["required_pct"] == 100  # untouched


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
    assert len(await _titles(api, status="active")) == len(DEFAULT)  # nothing sent or hidden
    summary = (await api.get("/api/listings/summary")).json()
    assert summary["locations"] and all(isinstance(p, str) for p in summary["locations"])
