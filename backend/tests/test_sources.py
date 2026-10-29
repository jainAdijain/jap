import httpx
import pytest

from schemas.job import ATS
from sources import ashby, greenhouse, lever
from sources.base import BoardNotFound, SourceChanged, SourceUnavailable
from sources.detect import NotABoard, detect, name_tokens, parse_url
from tests import boards as b

GH = "https://boards-api.greenhouse.io/v1/boards"
LV = "https://api.lever.co/v0/postings"
AS = "https://api.ashbyhq.com/posting-api/job-board"


def http(routes: b.Routes) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=b.transport(routes))


async def test_greenhouse_maps_direct_fields() -> None:
    async with http({f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB)}) as h:
        [job] = await greenhouse.fetch(h, "discord", "Discord")
    assert job.source is ATS.greenhouse and job.external_id == "8806482002"
    assert job.company_name == "Discord" and job.title == "Commercial Policy Lead"
    assert job.locations == ["San Francisco Bay Area", "Remote (US)"]
    assert job.department == "Policy" and job.requisition_id == "R-107406"
    assert job.posted_at is not None and job.posted_at.year == 2026
    # Escaped once more by Greenhouse; stored as real HTML.
    assert job.description_html.startswith("<div><p>Discord")
    assert job.raw_payload["id"] == 8806482002


async def test_lever_maps_direct_fields() -> None:
    async with http({f"{LV}/spotify": [b.LEVER_JOB]}) as h:
        [job] = await lever.fetch(h, "spotify", "Spotify")
    assert job.company_name == "Spotify"  # Lever has no company field: the board's name
    assert job.title.startswith("Account Executive")
    assert job.locations == ["New York, NY"] and job.employment_type == "Permanent"
    assert job.posted_at is not None and job.posted_at.year == 2026
    assert "What You'll Do" in job.description_html and "$87,069" in job.description_html
    assert job.salary is None  # only free text; left for extraction


async def test_lever_empty_board_is_not_an_error() -> None:
    async with http({f"{LV}/mistral": []}) as h:
        assert await lever.fetch(h, "mistral", "Mistral") == []


async def test_ashby_maps_salary_and_skips_unlisted() -> None:
    hidden = b.job(b.ASHBY_JOB, id="hidden", isListed=False)
    async with http({f"{AS}/ramp": b.ashby(b.ASHBY_JOB, hidden)}) as h:
        [job] = await ashby.fetch(h, "ramp", "Ramp")
    assert job.locations == ["New York, NY", "San Francisco, CA"]
    assert job.employment_type == "Full-time"
    assert job.salary is not None
    assert (job.salary.min, job.salary.max, job.salary.currency, job.salary.period) == (
        211400, 290600, "USD", "year",
    )  # fmt: skip


async def test_errors_are_sorted_for_retries() -> None:
    async with http({f"{GH}/gone/jobs": 404, f"{GH}/down/jobs": 503}) as h:
        with pytest.raises(BoardNotFound):
            await greenhouse.fetch(h, "gone", "Gone")
        with pytest.raises(SourceUnavailable):
            await greenhouse.fetch(h, "down", "Down")
    broken = b.job(b.GREENHOUSE_JOB)
    del broken["title"]
    async with http({f"{GH}/odd/jobs": b.greenhouse(broken)}) as h:
        with pytest.raises(SourceChanged):
            await greenhouse.fetch(h, "odd", "Odd")


@pytest.mark.parametrize(
    ("url", "ats", "token"),
    [
        ("https://boards.greenhouse.io/Discord", ATS.greenhouse, "discord"),
        ("job-boards.greenhouse.io/discord/jobs/8806482002", ATS.greenhouse, "discord"),
        ("https://boards.greenhouse.io/embed/job_board?for=figma", ATS.greenhouse, "figma"),
        ("https://jobs.lever.co/spotify/2a55d6c9", ATS.lever, "spotify"),
        ("https://jobs.ashbyhq.com/ramp", ATS.ashby, "ramp"),
        ("https://api.ashbyhq.com/posting-api/job-board/linear", ATS.ashby, "linear"),
    ],
)
def test_parse_url(url: str, ats: ATS, token: str) -> None:
    assert parse_url(url) == (ats, token)


def test_parse_url_rejects_other_sites() -> None:
    with pytest.raises(NotABoard, match="not a Greenhouse, Lever or Ashby board"):
        parse_url("https://www.linkedin.com/jobs/view/123")
    with pytest.raises(NotABoard, match="No board name"):
        parse_url("https://jobs.lever.co/")


def test_name_tokens() -> None:
    assert name_tokens("Acme Labs") == ["acmelabs", "acme-labs"]
    assert name_tokens("Ramp") == ["ramp"]


async def test_detect_url_checks_the_board() -> None:
    routes: b.Routes = {
        f"{GH}/discord/jobs": b.greenhouse(b.GREENHOUSE_JOB),
        f"{GH}/discord": {"name": "Discord", "content": "<p>About</p>"},
    }
    async with http(routes) as h:
        found = await detect(h, "https://boards.greenhouse.io/discord")
        assert (found.ats, found.board_token, found.display_name, found.job_count) == (
            ATS.greenhouse, "discord", "Discord", 1,
        )  # fmt: skip
        with pytest.raises(NotABoard, match="Greenhouse has no board called madeup"):
            await detect(h, "https://boards.greenhouse.io/madeup")


async def test_detect_name_tries_each_ats() -> None:
    async with http({f"{AS}/ramp": b.ashby(b.ASHBY_JOB)}) as h:
        found = await detect(h, "Ramp")
        assert (found.ats, found.board_token, found.display_name) == (ATS.ashby, "ramp", "Ramp")
        with pytest.raises(NotABoard, match="No Greenhouse, Lever or Ashby board found"):
            await detect(h, "Nobody Here Inc")
