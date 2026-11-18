"""The Claude reader's only door into JAP (PRD 1.25, R1 to R7): export and import.

With JAP_READER=claude (the default) JAP no longer sends job descriptions to OpenRouter for
reading (I3). A scheduled Claude session on Aditya's Claude plan reads them instead, a batch
at a time, 5 minutes after the daily search and before the writer (PRD 1.22). It never reads
the database. `export` gives it one batch of unread postings: open, company followed, and
description changed since it was last read (the I4 hash). Each comes with the fields still
wanted (those the ATS did not state, sources/workplace.py), its title, locations and
description, redacted as llm.call() redacts the extract prompt, and the rules of that prompt
(llm/prompts/extract.v1.md) so both readers read alike. guard() refuses the export if
anything personal is left. Nothing about me is in it: no resume, profile or answers.

`import` checks each posting's fields against the extraction schema (schemas/extract.py):
every wanted field, nothing else, only allowed values. A posting that fails, or whose
description changed since the export, is not saved and stays unread for the next batch. One
that passes is saved through the same step as OpenRouter's reads (enrich.save_extracted),
and its listing is refreshed and matched again. Nothing here applies or sends.

    python -m pipeline.claude_reader export [--limit 25]   > postings.json
    python -m pipeline.claude_reader import                < fields.json

fields.json: {"<posting id>": {"description_hash": "<from the export>", "skills": [...], ...}}
"""

import asyncio
import json
import sys
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Posting, Profile, Run, WatchedBoard
from database.session import get_sessionmaker
from llm.call import load_prompt
from llm.redact import Sensitive, guard, redact
from pipeline.dedup import refresh_listings
from pipeline.enrich import (
    MAX_JD_CHARS,
    direct_fields,
    save_extracted,
    wanted_fields,
    wanted_posting,
)
from pipeline.matching import refilter_from_profile
from pipeline.resume import sensitive_from_profile
from schemas.extract import FIELDS, from_output, output_model

BATCH = 25
IDS = ("posting_id", "description_hash")

REPLY = (
    "Reply with one JSON object, keyed by posting_id. Each value is an object holding the "
    "posting's description_hash exactly as given and every key in its keys list, with the "
    "types and values in field_schema, and nothing else. Leave out a posting you cannot read; "
    "it stays unread for the next batch."
)


def output_keys(fields: list[str]) -> list[str]:
    """The flat keys the reader answers for these fields (salary is four)."""
    return list(output_model(fields).model_fields)


def build_posting(posting: Posting, wanted: list[str], values: Sensitive) -> dict[str, Any]:
    return {
        "posting_id": str(posting.id),
        "description_hash": posting.description_hash,
        "keys": output_keys(wanted),
        "title": redact(posting.title_raw, values).text,
        "locations": [redact(loc, values).text for loc in posting.locations] or ["not stated"],
        "description_text": redact(
            posting.description_text[:MAX_JD_CHARS], values, generic=True
        ).text,
    }


def build_export(
    postings: list[dict[str, Any]], unread: int, run_active: bool, values: Sensitive
) -> dict[str, Any]:
    """The payload; raises PersonalDataBlocked if any personal value survives redaction."""
    out = {
        "instructions": load_prompt("extract").system,
        "reply_format": REPLY,
        "field_schema": output_model(FIELDS).model_json_schema(),
        "run_active": run_active,
        "unread_total": unread,
        "postings": postings,
    }
    # Ids and hashes are left out of the check: their digits run together could look like a
    # phone number to guard(), and they are JAP's own, never personal.
    text = [{k: v for k, v in p.items() if k not in IDS} for p in postings]
    guard(json.dumps([out["instructions"], text], ensure_ascii=False), values)
    return out


def _unread() -> Any:
    followed = select(WatchedBoard.id).where(WatchedBoard.active)
    return (
        Posting.state == "open",
        Posting.watched_board_id.in_(followed),
        or_(Posting.extracted_hash.is_(None), Posting.extracted_hash != Posting.description_hash),
        func.length(func.trim(Posting.description_text)) > 0,
    )


async def export(session: AsyncSession, limit: int = BATCH) -> dict[str, Any]:
    profile = await session.scalar(select(Profile))
    values = sensitive_from_profile(profile)
    unread = int(await session.scalar(select(func.count()).where(*_unread())) or 0)
    run_active = (
        await session.scalar(select(Run.id).where(Run.status.in_(("queued", "running"))).limit(1))
        is not None
    )
    rows = await session.scalars(
        select(Posting).where(*_unread()).order_by(Posting.first_seen_at, Posting.id)
    )
    batch: list[dict[str, Any]] = []
    for posting in rows:
        direct = direct_fields(posting.source, posting.raw_payload, posting.locations)
        wanted = wanted_fields(direct)
        if not wanted:
            continue  # the ATS stated everything: enrich saves it without a reader
        batch.append(build_posting(posting, wanted, values))
        if len(batch) >= limit:
            break
    return build_export(batch, unread, run_active, values)


class NotSaved(Exception):
    pass


def check_fields(wanted: list[str], given: dict[str, Any]) -> dict[str, Any]:
    """The reader's fields as JAP stores them, after the extraction schema's check."""
    keys = set(output_keys(wanted))
    extra = sorted(set(given) - keys)
    if extra:
        raise NotSaved(f"unknown or not wanted: {', '.join(extra)}")
    missing = sorted(keys - set(given))
    if missing:
        raise NotSaved(f"missing: {', '.join(missing)}")
    try:
        out = output_model(wanted).model_validate(given)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise NotSaved(f"{where}: {first['msg']}") from exc
    return from_output(out)


async def import_one(session: AsyncSession, posting_id: str, given: dict[str, Any]) -> Posting:
    try:
        pid = uuid.UUID(posting_id)
    except ValueError as exc:
        raise NotSaved("not a posting id") from exc
    if not isinstance(given, dict):
        raise NotSaved("not an object of fields")
    fields = dict(given)
    description_hash = fields.pop("description_hash", None)
    posting = await wanted_posting(session, pid)
    if posting is None:
        raise NotSaved("posting closed, unfollowed or gone")
    if description_hash != posting.description_hash:
        raise NotSaved("description changed since the export (or no description_hash)")
    if posting.extracted_hash == posting.description_hash:
        raise NotSaved("already read")
    direct = direct_fields(posting.source, posting.raw_payload, posting.locations)
    read = check_fields(wanted_fields(direct), fields)
    if not await save_extracted(session, pid, posting.description_hash, direct, read):
        raise NotSaved("description changed while saving")
    return posting


async def import_fields(session: AsyncSession, replies: dict[str, Any]) -> list[str]:
    """Save each posting that passes; one line per posting, then a total."""
    if not isinstance(replies, dict):
        raise SystemExit("fields.json must be one JSON object keyed by posting id")
    out: list[str] = []
    listings: set[uuid.UUID] = set()
    for posting_id, given in replies.items():
        try:
            posting = await import_one(session, posting_id, given)
        except NotSaved as exc:
            await session.rollback()
            out.append(f"{posting_id}: not saved ({exc})")
            continue
        out.append(f"{posting_id}: saved")
        if posting.listing_id is not None:
            listings.add(posting.listing_id)
    if listings:
        await refresh_listings(session, listings)
        await refilter_from_profile(session, list(listings))  # new skills, work mode
        await session.commit()
    saved = sum(line.endswith(": saved") for line in out)
    out.append(f"saved {saved} of {len(replies)}")
    return out


async def _main(argv: list[str]) -> None:
    command = argv[0] if argv else ""
    async with get_sessionmaker()() as session:
        if command == "export":
            limit = int(argv[2]) if len(argv) > 2 and argv[1] == "--limit" else BATCH
            print(json.dumps(await export(session, max(1, limit)), ensure_ascii=False, indent=1))
        elif command == "import":
            for line in await import_fields(session, json.loads(sys.stdin.read())):
                print(line)
        else:
            raise SystemExit("usage: python -m pipeline.claude_reader export [--limit N]|import")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
