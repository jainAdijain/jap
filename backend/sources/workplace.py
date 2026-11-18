"""Work mode and remote regions as the ATS states them (I2), with no model call (JAP-10).

Each ATS says "remote" its own way:
- Ashby: workplaceType (Remote, Hybrid, OnSite), and isRemote when workplaceType is missing.
- Lever: workplaceType (remote, hybrid, on-site; unspecified says nothing).
- Greenhouse: no field; a location entry such as "Remote", "Remote - India" or
  "India (Remote)". The same words count on Ashby and Lever when they give no flag.

When the job is remote, its remote regions are what is left of a remote entry once "remote"
is taken out ("Remote - India" gives India, and the San Francisco office beside "Remote (US)"
is not a region), or else the other entries ("India" beside "Remote", or Ashby's "India"
with isRemote). A job that says only "Remote" gives no region, so the model is still asked.

These are read again from the stored payload every time a listing is refreshed
(pipeline/dedup.py), so a job is matched on them even when extraction never ran, as when the
OpenRouter key is out of credit.
"""

import re
from typing import Any

WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}
_REMOTE = re.compile(r"(^|[^a-z])remote([^a-z]|$)")
_REMOTE_WORD = re.compile(r"\b(fully\s+|100%\s+)?remote([\s-]+(first|friendly|only|ok))?\b", re.I)
_EDGES = re.compile(r"^[\s\-–—,:;/|()\[\]]+|[\s\-–—,:;/|()\[\]]+$")
_NOT_A_PLACE = {"", "remote", "wfh", "work from home"}  # Worldwide is kept: it says anywhere


def says_remote(location: str) -> bool:
    low = location.lower()
    return bool(_REMOTE.search(low)) and "hybrid" not in low


def work_mode(source: str, raw: dict[str, Any], locations: list[str]) -> str | None:
    """remote, hybrid or onsite when the ATS says so, else None (left to the model)."""
    if source in ("ashby", "lever"):
        mode = WORKPLACE.get(str(raw.get("workplaceType") or "").lower())
        if mode:
            return mode
    if source == "ashby" and raw.get("isRemote") is True:
        return "remote"
    if any(says_remote(loc) for loc in locations):
        return "remote"
    return None


def _place(location: str) -> str:
    place = _EDGES.sub("", _REMOTE_WORD.sub(" ", location)) if says_remote(location) else location
    place = re.sub(r"\s{2,}", " ", place.strip())
    return "" if place.lower() in _NOT_A_PLACE else place


def remote_regions(locations: list[str]) -> list[str]:
    """The places a remote job's location entries name, in order, without repeats. Entries
    that name their remote region ("Remote (US)") are used alone, so the office beside them
    is not taken for one; otherwise every entry's place counts ("India" beside "Remote")."""
    named = [p for loc in locations if says_remote(loc) and (p := _place(loc))]
    places = named or [p for loc in locations if (p := _place(loc))]
    return list(dict.fromkeys(places))


def stated(source: str, raw: dict[str, Any], locations: list[str]) -> dict[str, Any]:
    """{"work_mode": ..., "remote_regions": [...]} with only what the ATS states."""
    out: dict[str, Any] = {}
    mode = work_mode(source, raw, locations)
    if mode:
        out["work_mode"] = mode
        if mode == "remote" and (regions := remote_regions(locations)):
            out["remote_regions"] = regions
    return out
