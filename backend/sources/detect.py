"""Adding a company (I9): a board URL or a company name in, (ATS, board token) out.

A URL names its ATS: boards.greenhouse.io/<token>, job-boards.greenhouse.io/<token>,
jobs.lever.co/<token> or jobs.ashbyhq.com/<token>. A plain name is tried as a token on
each ATS in turn. Either way the board must answer before it can be saved.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

from schemas.job import ATS
from sources import ADAPTERS
from sources.base import BoardNotFound, SourceChanged, SourceUnavailable

TOKEN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
HOSTS: dict[str, ATS] = {
    "boards.greenhouse.io": ATS.greenhouse,
    "job-boards.greenhouse.io": ATS.greenhouse,
    "job-boards.eu.greenhouse.io": ATS.greenhouse,
    "boards-api.greenhouse.io": ATS.greenhouse,
    "jobs.lever.co": ATS.lever,
    "api.lever.co": ATS.lever,
    "jobs.ashbyhq.com": ATS.ashby,
    "api.ashbyhq.com": ATS.ashby,
}
# Path segments that come before the token in API-style URLs.
SKIP = {"v1", "v0", "boards", "postings", "posting-api", "job-board", "embed"}


class NotABoard(ValueError):
    """The text is neither a supported board URL nor a name any ATS knows."""


@dataclass
class Detected:
    ats: ATS
    board_token: str
    display_name: str
    job_count: int


def parse_url(text: str) -> tuple[ATS, str]:
    url = urlparse(text if "://" in text else f"https://{text}")
    host = (url.hostname or "").lower().removeprefix("www.")
    ats = HOSTS.get(host)
    if ats is None:
        raise NotABoard(
            f"{host or text} is not a Greenhouse, Lever or Ashby board. In v1 JAP reads "
            "only boards.greenhouse.io, jobs.lever.co and jobs.ashbyhq.com"
        )
    for_param = parse_qs(url.query).get("for")  # boards.greenhouse.io/embed/job_board?for=x
    parts = [p for p in url.path.split("/") if p and p not in SKIP]
    token = for_param[0] if for_param else (parts[0] if parts else "")
    if not TOKEN.match(token):
        raise NotABoard(f"No board name found in {text}")
    return ats, token.lower()


def looks_like_url(text: str) -> bool:
    return " " not in text and ("/" in text or "." in text)


def name_tokens(name: str) -> list[str]:
    """Acme Labs -> acmelabs, acme-labs."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    out = ["".join(words), "-".join(words)]
    return [t for i, t in enumerate(out) if t and t not in out[:i]]


def pretty(token: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_.]+", token) if w) or token


async def detect(http: httpx.AsyncClient, text: str) -> Detected:
    text = text.strip()
    if not text:
        raise NotABoard("Paste a board URL or type a company name")
    if looks_like_url(text):
        candidates = [parse_url(text)]
        given_name = None
    else:
        given_name = text
        candidates = [(ats, t) for ats in ATS for t in name_tokens(text)]
    for ats, token in candidates:
        try:
            info = await ADAPTERS[ats].board_info(http, token)
        except BoardNotFound:
            continue
        except (SourceUnavailable, SourceChanged) as exc:
            raise NotABoard(f"{ats.value.capitalize()} did not answer for {token}: {exc}") from exc
        return Detected(
            ats=ats,
            board_token=token,
            display_name=info.name or given_name or pretty(token),
            job_count=info.job_count,
        )
    if given_name is None:
        ats, token = candidates[0]
        raise NotABoard(f"{ats.value.capitalize()} has no board called {token}")
    raise NotABoard(
        f"No Greenhouse, Lever or Ashby board found for {text}. Paste the board URL instead"
    )
