"""Board payloads shaped like the live APIs (checked 29 Oct 2026), and a fake HTTP layer."""

import copy
import html
from typing import Any

import httpx
import pytest

GH_CONTENT = html.escape(
    "<div><p>Discord is a voice, video and text platform.</p>"
    "<h3>What you'll do</h3><ul><li>Own commercial policy</li><li>Work with Legal</li></ul>"
    "<p>Remote in the US. Python and SQL a plus.</p></div>"
)
GREENHOUSE_JOB: dict[str, Any] = {
    "absolute_url": "https://job-boards.greenhouse.io/discord/jobs/8806482002",
    "internal_job_id": 6524134002,
    "location": {"name": "San Francisco Bay Area"},
    "metadata": [],
    "id": 8806482002,
    "updated_at": "2026-10-20T20:27:20-04:00",
    "requisition_id": "R-107406",
    "title": "Commercial Policy Lead",
    "company_name": "Discord",
    "first_published": "2026-09-15T12:52:11-04:00",
    "language": "en",
    "content": GH_CONTENT,
    "departments": [{"id": 4054683002, "name": "Policy", "child_ids": [], "parent_id": None}],
    "offices": [{"id": 4082816002, "name": "Remote (US)", "location": None}],
}
LEVER_JOB: dict[str, Any] = {
    "additionalPlain": "The United States base range for this position is $87,069-$124,385.",
    "additional": "<div>The United States base range for this position is $87,069-$124,385.</div>",
    "categories": {
        "commitment": "Permanent",
        "department": "Operations and Business Support",
        "location": "New York, NY",
        "team": "Platform",
        "allLocations": ["New York, NY"],
    },
    "createdAt": 1792767640768,
    "descriptionPlain": "Spotify Technology is our business-to-business software team.",
    "description": "<div><p>Spotify Technology is our B2B software team.</p></div>",
    "id": "2a55d6c9-c564-443a-be3e-a3851db5a93b",
    "lists": [{"text": "What You'll Do", "content": "<li>Find new business</li>"}],
    "text": "Account Executive - Commercial, Spotify Technology",
    "country": "US",
    "workplaceType": "hybrid",
    "hostedUrl": "https://jobs.lever.co/spotify/2a55d6c9-c564-443a-be3e-a3851db5a93b",
    "applyUrl": "https://jobs.lever.co/spotify/2a55d6c9-c564-443a-be3e-a3851db5a93b/apply",
}
ASHBY_JOB: dict[str, Any] = {
    "id": "d3bc1ced-3ce4-4086-a050-555055dbb1ff",
    "title": "Security Engineer, Cloud",
    "department": "Engineering",
    "team": "Security",
    "employmentType": "FullTime",
    "location": "New York, NY",
    "secondaryLocations": [{"location": "San Francisco, CA"}],
    "publishedAt": "2026-10-01T20:13:45.158+00:00",
    "isListed": True,
    "isRemote": False,
    "workplaceType": "Hybrid",
    "jobUrl": "https://jobs.ashbyhq.com/ramp/d3bc1ced-3ce4-4086-a050-555055dbb1ff",
    "applyUrl": "https://jobs.ashbyhq.com/ramp/d3bc1ced-3ce4-4086-a050-555055dbb1ff/application",
    "descriptionHtml": "<p style=\"min-height:1.5em\">Secure our AWS and Kubernetes estate.</p>",
    "descriptionPlain": "Secure our AWS and Kubernetes estate.",
    "compensation": {
        "compensationTierSummary": "$211.4K – $290.6K • Offers Equity",
        "summaryComponents": [
            {"compensationType": "EquityPercentage", "interval": "NONE", "currencyCode": None,
             "minValue": None, "maxValue": None},
            {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD",
             "minValue": 211400, "maxValue": 290600},
        ],
    },
}  # fmt: skip


def greenhouse(*jobs: dict[str, Any]) -> dict[str, Any]:
    return {"jobs": list(jobs), "meta": {"total": len(jobs)}}


def ashby(*jobs: dict[str, Any]) -> dict[str, Any]:
    return {"jobs": list(jobs), "apiVersion": "1"}


def job(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    out = copy.deepcopy(base)
    out.update(changes)
    return out


Routes = dict[str, Any]  # URL prefix -> JSON body, or an int status code


def transport(routes: Routes) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for prefix, body in sorted(routes.items(), key=lambda kv: -len(kv[0])):
            if url.startswith(prefix):
                if isinstance(body, int):
                    return httpx.Response(body, text="Not Found")
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def fake_boards(monkeypatch: pytest.MonkeyPatch, routes: Routes) -> None:
    """Point every adapter's HTTP client at routes (change routes in place to change them)."""

    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport(routes))

    import sources.base

    monkeypatch.setattr(sources.base, "client", make)
