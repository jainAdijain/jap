"""Lever public postings API: api.lever.co/v0/postings/{token}?mode=json.

The response is a bare array. A real board with no openings answers 200 and [], an
unknown one 404. The title is "text", createdAt is epoch milliseconds, and pay is only
free text in "additional", so salary is left for extraction.
"""

from datetime import UTC, datetime
from typing import Any

import httpx

from schemas.job import ATS, Job
from sources.base import Adapter, BoardInfo, SourceChanged, build, get_json

API = "https://api.lever.co/v0/postings"


def description_html(job: dict[str, Any]) -> str:
    parts = [job.get("description") or ""]
    for item in job.get("lists") or []:
        parts.append(f"<h3>{item.get('text', '')}</h3>{item.get('content', '')}")
    parts.append(job.get("additional") or "")
    return "\n".join(p for p in parts if p)


def to_job(job: dict[str, Any], display_name: str) -> Job:
    cats = job.get("categories") or {}
    locations = [loc for loc in cats.get("allLocations") or [] if loc]
    if not locations and cats.get("location"):
        locations = [cats["location"]]
    created = job.get("createdAt")
    return Job(
        source=ATS.lever,
        external_id=job["id"],
        company_name=display_name,
        title=job["text"].strip(),
        locations=locations,
        department=cats.get("department") or cats.get("team") or None,
        employment_type=cats.get("commitment") or None,
        posted_at=datetime.fromtimestamp(created / 1000, tz=UTC) if created else None,
        posting_url=job["hostedUrl"],
        apply_url=job.get("applyUrl") or job["hostedUrl"],
        requisition_id=None,
        description_html=description_html(job),
        raw_payload=job,
    )


async def fetch(http: httpx.AsyncClient, token: str, display_name: str) -> list[Job]:
    data = await get_json(http, f"{API}/{token}?mode=json")
    if not isinstance(data, list):
        raise SourceChanged("Lever: the response is not a list of postings")
    return [build("Lever", to_job, j, display_name) for j in data]


async def board_info(http: httpx.AsyncClient, token: str) -> BoardInfo:
    data = await get_json(http, f"{API}/{token}?mode=json")
    return BoardInfo(name=None, job_count=len(data) if isinstance(data, list) else 0)


ADAPTER = Adapter(host="api.lever.co", fetch=fetch, board_info=board_info)
