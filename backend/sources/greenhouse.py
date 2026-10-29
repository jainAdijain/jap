"""Greenhouse public board API: boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true.

content is HTML with its entities escaped once more (&lt;p&gt;), so it is unescaped here
and stored as real HTML. company_name comes with every job; there is no salary field.
"""

import html
from datetime import datetime
from typing import Any

import httpx

from schemas.job import ATS, Job
from sources.base import Adapter, BoardInfo, SourceChanged, build, get_json

API = "https://boards-api.greenhouse.io/v1/boards"


def _locations(job: dict[str, Any]) -> list[str]:
    out: list[str] = []
    name = (job.get("location") or {}).get("name")
    for value in [name, *(o.get("name") for o in job.get("offices") or [])]:
        if value and value not in out:
            out.append(value)
    return out


def to_job(job: dict[str, Any], display_name: str) -> Job:
    departments = [d["name"] for d in job.get("departments") or [] if d.get("name")]
    posted = job.get("first_published") or job.get("updated_at")
    return Job(
        source=ATS.greenhouse,
        external_id=str(job["id"]),
        company_name=job.get("company_name") or display_name,
        title=job["title"].strip(),
        locations=_locations(job),
        department=departments[0] if departments else None,
        employment_type=None,
        posted_at=datetime.fromisoformat(posted) if posted else None,
        posting_url=job["absolute_url"],
        apply_url=job["absolute_url"],
        requisition_id=job.get("requisition_id") or None,
        description_html=html.unescape(job.get("content") or ""),
        raw_payload=job,
    )


async def fetch(http: httpx.AsyncClient, token: str, display_name: str) -> list[Job]:
    data = await get_json(http, f"{API}/{token}/jobs?content=true")
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceChanged("Greenhouse: no jobs list in the response")
    return [build("Greenhouse", to_job, j, display_name) for j in data["jobs"]]


async def board_info(http: httpx.AsyncClient, token: str) -> BoardInfo:
    board = await get_json(http, f"{API}/{token}")
    jobs = await get_json(http, f"{API}/{token}/jobs")
    return BoardInfo(name=board.get("name"), job_count=len(jobs.get("jobs") or []))


ADAPTER = Adapter(host="boards-api.greenhouse.io", fetch=fetch, board_info=board_info)
