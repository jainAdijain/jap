"""Ashby public job board API: api.ashbyhq.com/posting-api/job-board/{token}.

includeCompensation=true adds a structured salary when the company shows one; that is the
only ATS that gives salary directly (I2). Unlisted jobs are skipped.
"""

from datetime import datetime
from typing import Any

import httpx

from schemas.job import ATS, Job, Salary
from sources.base import Adapter, BoardInfo, SourceChanged, build, get_json

API = "https://api.ashbyhq.com/posting-api/job-board"
INTERVALS = {"1 YEAR": "year", "1 MONTH": "month", "1 WEEK": "week",
             "1 DAY": "day", "1 HOUR": "hour"}  # fmt: skip
EMPLOYMENT = {"FullTime": "Full-time", "PartTime": "Part-time", "Intern": "Intern",
              "Contract": "Contract", "Temporary": "Temporary"}  # fmt: skip


def salary(job: dict[str, Any]) -> Salary | None:
    comp = job.get("compensation") or {}
    for part in comp.get("summaryComponents") or []:
        has_number = part.get("minValue") or part.get("maxValue")
        if part.get("compensationType") == "Salary" and has_number:
            return Salary(
                min=part.get("minValue"),
                max=part.get("maxValue"),
                currency=part.get("currencyCode"),
                period=INTERVALS.get(part.get("interval") or ""),
            )
    return None


def to_job(job: dict[str, Any], display_name: str) -> Job:
    locations = [job["location"]] if job.get("location") else []
    for extra in job.get("secondaryLocations") or []:
        name = extra.get("location")
        if name and name not in locations:
            locations.append(name)
    published = job.get("publishedAt")
    kind = job.get("employmentType")
    return Job(
        source=ATS.ashby,
        external_id=job["id"],
        company_name=display_name,
        title=job["title"].strip(),
        locations=locations,
        department=job.get("department") or job.get("team") or None,
        employment_type=EMPLOYMENT.get(kind, kind) if kind else None,
        posted_at=datetime.fromisoformat(published) if published else None,
        posting_url=job["jobUrl"],
        apply_url=job.get("applyUrl") or job["jobUrl"],
        requisition_id=None,
        description_html=job.get("descriptionHtml") or "",
        raw_payload=job,
        salary=salary(job),
    )


async def fetch(http: httpx.AsyncClient, token: str, display_name: str) -> list[Job]:
    data = await get_json(http, f"{API}/{token}?includeCompensation=true")
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceChanged("Ashby: no jobs list in the response")
    listed = [j for j in data["jobs"] if j.get("isListed", True)]
    return [build("Ashby", to_job, j, display_name) for j in listed]


async def board_info(http: httpx.AsyncClient, token: str) -> BoardInfo:
    data = await get_json(http, f"{API}/{token}")
    jobs = [j for j in data.get("jobs") or [] if j.get("isListed", True)]
    return BoardInfo(name=None, job_count=len(jobs))


ADAPTER = Adapter(host="api.ashbyhq.com", fetch=fetch, board_info=board_info)
