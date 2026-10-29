"""What every ATS adapter implements, and the three errors it may raise (design doc)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from schemas.job import Job

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
USER_AGENT = "JAP/0.3 (personal job search; one request per board per run)"


class BoardNotFound(Exception):
    """404: the board does not exist. The board is marked last_ok = false, no retry."""


class SourceUnavailable(Exception):
    """5xx, 429 or a timeout. Inngest retries the step (2 retries)."""


class SourceChanged(Exception):
    """The payload no longer fits Job. The step fails and the run says why."""


@dataclass
class BoardInfo:
    name: str | None  # the company name the ATS shows, when it has one
    job_count: int


@dataclass
class Adapter:
    host: str  # for the 1 request per second per host throttle (I7)
    fetch: Callable[[httpx.AsyncClient, str, str], Awaitable[list[Job]]]
    board_info: Callable[[httpx.AsyncClient, str], Awaitable[BoardInfo]]


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )


async def get_json(http: httpx.AsyncClient, url: str) -> Any:
    try:
        resp = await http.get(url)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"{url}: {type(exc).__name__}") from exc
    if resp.status_code == 404:
        raise BoardNotFound(url)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise SourceUnavailable(f"{url}: HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise SourceChanged(f"{url}: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceChanged(f"{url}: response is not JSON") from exc


def build(
    source: str, mapper: Callable[[dict[str, Any], str], Job], job: dict[str, Any], name: str
) -> Job:
    """Map one payload to Job, turning any mismatch into SourceChanged."""
    try:
        return mapper(job, name)
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise SourceChanged(f"{source} payload no longer fits Job: {exc}") from exc
