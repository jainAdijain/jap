"""JAP-10: work mode and remote regions as the ATS states them, with no model call.

A remote job saved with no work mode failed the Location condition when the model never
read it (OpenRouter out of credit). These come from the payload instead, and every listing
refresh reads them again, so a run with no extraction still lists the job.
"""

from typing import Any

import httpx
import psycopg
import pytest

from database.session import get_sessionmaker
from functions.fetch_board import fetch_step
from pipeline.dedup import dedup
from pipeline.matching import refilter_from_profile
from sources import workplace
from tests import boards as b
from tests.test_ingest import _board

GH = "https://boards-api.greenhouse.io/v1/boards"
AS = "https://api.ashbyhq.com/posting-api/job-board"
LV = "https://api.lever.co/v0/postings"


HYBRID, ONSITE = {"work_mode": "hybrid"}, {"work_mode": "onsite"}


def _remote(*regions: str) -> dict[str, Any]:
    return {"work_mode": "remote", **({"remote_regions": list(regions)} if regions else {})}


@pytest.mark.parametrize(
    ("source", "raw", "locations", "expected"),
    [
        # Ashby: isRemote with a plain "India" (Atlan), and workplaceType winning over it.
        ("ashby", {"isRemote": True}, ["India"], _remote("India")),
        ("ashby", {"workplaceType": "Remote"}, ["India"], _remote("India")),
        ("ashby", {"workplaceType": "Hybrid", "isRemote": True}, ["India"], HYBRID),
        ("ashby", {"workplaceType": "OnSite", "isRemote": False}, ["Bengaluru"], ONSITE),
        ("ashby", {"isRemote": False}, ["Bengaluru"], {}),
        # Lever: workplaceType; unspecified says nothing.
        ("lever", {"workplaceType": "remote"}, ["India"], _remote("India")),
        ("lever", {"workplaceType": "on-site"}, ["Pune"], ONSITE),
        ("lever", {"workplaceType": "unspecified"}, ["Pune"], {}),
        # Greenhouse: separate "India" and "Remote" entries (Together AI).
        ("greenhouse", {}, ["India", "Remote"], _remote("India")),
        ("greenhouse", {}, ["Remote - India"], _remote("India")),
        ("greenhouse", {}, ["Bengaluru, India (Remote)"], _remote("Bengaluru, India")),
        # A remote entry that names its region: the office beside it is not a region.
        ("greenhouse", {}, ["San Francisco Bay Area", "Remote (US)"], _remote("US")),
        ("greenhouse", {}, ["Remote - Worldwide"], _remote("Worldwide")),
        ("greenhouse", {}, ["Remote"], _remote()),  # regions left to the model
        ("greenhouse", {}, ["Hybrid - Remote, London"], {}),  # not plainly remote
        ("greenhouse", {}, ["Remoteville, OR"], {}),  # a word, not a place called remote
        ("greenhouse", {"workplaceType": "remote"}, ["Pune"], {}),  # no such Greenhouse field
    ],
)  # fmt: skip
def test_stated(source: str, raw: dict[str, Any], locations: list[str], expected: Any) -> None:
    assert workplace.stated(source, raw, locations) == expected


PROFILE: dict[str, Any] = {
    "target_roles": ["Backend Engineer"],
    "locations": ["Bangalore"],
    "remote_regions": ["India"],
    "match_rule": [["location"]],
}


async def test_remote_india_jobs_match_with_no_extraction(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three shapes from 18 Nov, fetched and placed with no model call at all."""
    atlan = b.job(b.ASHBY_JOB, id="atlan-1", title="AI Native Builder", location="India",
                  secondaryLocations=[], isRemote=True, workplaceType=None)  # fmt: skip
    onsite = b.job(b.ASHBY_JOB, id="atlan-2", title="Office Manager", location="India",
                   secondaryLocations=[], isRemote=False, workplaceType="OnSite")  # fmt: skip
    together = b.job(b.GREENHOUSE_JOB, id=1, title="Infra Agent Systems",
                     location={"name": "India"}, offices=[{"name": "Remote"}])  # fmt: skip
    lever = b.job(b.LEVER_JOB, id="lv-1", text="Platform Engineer", workplaceType="remote",
                  categories={**b.LEVER_JOB["categories"], "allLocations": ["India"]})  # fmt: skip
    b.fake_boards(
        monkeypatch,
        {
            f"{AS}/atlan": b.ashby(atlan, onsite),
            f"{GH}/together/jobs": b.greenhouse(together),
            f"{LV}/acme": [lever],
        },
    )
    await fetch_step(await _board("ashby", "atlan"))
    await fetch_step(await _board("greenhouse", "together"))
    await fetch_step(await _board("lever", "acme"))
    assert sql.execute("SELECT count(*) FROM posting WHERE extracted IS NULL").fetchone() == (4,)
    async with get_sessionmaker()() as session:
        await dedup(session)
        await session.commit()
    rows = dict(
        (t, (m, r))
        for t, m, r in sql.execute("SELECT title_display, work_mode, remote_regions FROM listing")
    )
    assert rows == {
        "AI Native Builder": ("remote", ["India"]),
        "Office Manager": ("onsite", []),
        "Infra Agent Systems": ("remote", ["India"]),
        "Platform Engineer": ("remote", ["India"]),
    }
    resp = await api.put("/api/profile", json=PROFILE)
    assert resp.status_code == 200, resp.text
    listed = {r["title"] for r in (await api.get("/api/listings")).json()["rows"]}
    assert listed == {"AI Native Builder", "Infra Agent Systems", "Platform Engineer"}

    # A later refresh after the model ran keeps what the ATS states over what it said.
    sql.execute(
        'UPDATE posting SET extracted = \'{"work_mode": "unknown", "remote_regions": ["APAC"]}\''
    )
    async with get_sessionmaker()() as session:
        from pipeline.dedup import refresh_listings

        ids = {r[0] for r in sql.execute("SELECT id FROM listing")}
        await refresh_listings(session, ids)
        await refilter_from_profile(session)
        await session.commit()
    row = sql.execute(
        "SELECT work_mode, remote_regions FROM listing WHERE title_display = 'AI Native Builder'"
    ).fetchone()
    assert row == ("remote", ["India", "APAC"])
