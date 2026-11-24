"""The AI reader's only door into JAP (PRD 1.25, R1 to R7; 1.30 AH1): export and import.

With JAP_READER=agent (the default; named claude before 1.30) JAP no longer sends job
descriptions to OpenRouter for reading (I3). An outside AI harness on Aditya's plan reads them
instead (agent/run.sh starts it, PRD 1.30), a batch at a time, in parallel shards for a big
backlog, 5 minutes after the daily search and before
the writer (PRD 1.22). It never reads the database. `export` gives it one batch of unread
postings: open, company followed, and description changed since it was last read (the I4
hash). Each comes with the fields still wanted (those the ATS did not state,
sources/workplace.py), its title, locations and description, redacted as llm.call() redacts
the extract prompt, and the rules of that prompt (llm/prompts/extract.v1.md) so both readers
read alike. guard() refuses the export if anything personal is left. Nothing about me is in
it: no resume, profile or answers.

`import` checks each posting's fields against the extraction schema (schemas/extract.py):
every wanted field, nothing else, only allowed values. A posting that fails, or whose
description changed since the export, is not saved and stays unread for the next batch. One
that passes is saved through the same step as OpenRouter's reads (enrich.save_extracted),
and its listing is refreshed and matched again. Nothing here applies or sends.

It also settles Possible duplicates (R8, PRD 1.28): shard 1's export carries the open pairs
(both postings' title, locations and redacted description, and the rules of JAP's same-job
prompt), and an import entry keyed "pair:<id>" with {"same": bool, "reason": "..."} merges
the pair or keeps it apart through the same step as before (dedup.apply_same).

    python -m pipeline.claude_reader export [--limit 50] [--shard 3/8]  > postings.json
    python -m pipeline.claude_reader import                < fields.json
    python -m pipeline.claude_reader status                (one line for agent/run.sh)

fields.json: {"<posting id>": {"description_hash": "<from the export>", "skills": [...], ...},
              "pair:<pair id>": {"same": false, "reason": "..."}}
"""

import asyncio
import json
import sys
import time
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import PossibleDuplicate, Posting, Profile, Run, WatchedBoard
from database.session import get_sessionmaker
from llm.call import load_prompt
from llm.redact import Sensitive, guard, redact
from pipeline import agent_doors
from pipeline.agent_contract import CONTRACT_VERSION
from pipeline.dedup import SameJob, apply_same, open_pairs, pair_postings, refresh_listings
from pipeline.enrich import (
    MAX_JD_CHARS,
    direct_fields,
    save_extracted,
    wanted_fields,
    wanted_posting,
)
from pipeline.matching import refilter_from_profile
from pipeline.resume import sensitive_from_profile
from pipeline.runs import in_scope, role_matcher
from schemas.extract import FIELDS, from_output, output_model

BATCH = 50
IDS = ("posting_id", "description_hash")
PAIR = "pair:"

REPLY = (
    "Reply with one JSON object, keyed by posting_id. Each value is an object holding the "
    "posting's description_hash exactly as given and every key in its keys list, with the "
    "types and values in field_schema, and nothing else. Leave out a posting you cannot read; "
    'it stays unread for the next batch. For each entry in pairs, add a key "pair:<pair_id>" '
    'holding {"same": true or false, "reason": one short sentence}, following '
    "pair_instructions; leave out a pair you cannot decide."
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


def build_pair(dup: PossibleDuplicate, a: Posting, b: Posting, values: Sensitive) -> dict[str, Any]:
    def side(p: Posting) -> dict[str, Any]:
        return {
            "title": redact(p.title_raw, values).text,
            "locations": [redact(loc, values).text for loc in p.locations] or ["not stated"],
            "description_text": redact(
                p.description_text[:MAX_JD_CHARS], values, generic=True
            ).text,
        }

    return {"pair_id": str(dup.id), "job_a": side(a), "job_b": side(b)}


def build_export(
    postings: list[dict[str, Any]],
    unread: int,
    run_active: bool,
    values: Sensitive,
    pairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The payload; raises PersonalDataBlocked if any personal value survives redaction."""
    pairs = pairs or []
    out: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "instructions": load_prompt("extract").system,
        "reply_format": REPLY,
        "field_schema": output_model(FIELDS).model_json_schema(),
        "run_active": run_active,
        "unread_total": unread,
        "postings": postings,
        "pair_instructions": load_prompt("same_job").system,
        "pairs": pairs,
    }
    # Ids and hashes are left out of the check: their digits run together could look like a
    # phone number to guard(), and they are JAP's own, never personal.
    text = [{k: v for k, v in p.items() if k not in IDS} for p in postings]
    sides = [[p["job_a"], p["job_b"]] for p in pairs]
    guard(json.dumps([out["instructions"], text, sides], ensure_ascii=False), values)
    return out


def _unread() -> Any:
    followed = select(WatchedBoard.id).where(WatchedBoard.active)
    return (
        Posting.state == "open",
        Posting.watched_board_id.in_(followed),
        or_(Posting.extracted_hash.is_(None), Posting.extracted_hash != Posting.description_hash),
        func.length(func.trim(Posting.description_text)) > 0,
    )


def in_shard(posting_id: uuid.UUID, shard: tuple[int, int]) -> bool:
    """Shard i of n (1-based): parallel readers each take a disjoint share of the postings."""
    i, n = shard
    return posting_id.int % n == i - 1


def parse_shard(text: str) -> tuple[int, int]:
    i, _, n = text.partition("/")
    shard = (int(i), int(n))
    if not 1 <= shard[0] <= shard[1]:
        raise SystemExit(f"--shard is i/n with 1 <= i <= n, not {text}")
    return shard


async def export(
    session: AsyncSession, limit: int = BATCH, shard: tuple[int, int] = (1, 1)
) -> dict[str, Any]:
    """One batch from this shard. Postings whose title is one of my target roles come
    first, so the jobs I can be listed for are read in the first minutes of a big backlog
    (R3); the rest follow, oldest first."""
    profile = await session.scalar(select(Profile))
    values = sensitive_from_profile(profile)
    run_active = (
        await session.scalar(select(Run.id).where(Run.status.in_(("queued", "running"))).limit(1))
        is not None
    )
    matcher = role_matcher(profile.target_roles if profile else None)
    heads = (
        await session.execute(
            select(Posting.id, Posting.title_raw)
            .where(*_unread())
            .order_by(Posting.first_seen_at, Posting.id)
        )
    ).all()
    mine = {pid for pid, title in heads if matcher is not None and in_scope(title, matcher)}
    order = [pid for pid, _ in heads if pid in mine] + [pid for pid, _ in heads if pid not in mine]
    order = [pid for pid in order if in_shard(pid, shard)]
    batch: list[dict[str, Any]] = []
    for start in range(0, len(order), limit):
        chunk = order[start : start + limit]
        rows = {
            p.id: p for p in await session.scalars(select(Posting).where(Posting.id.in_(chunk)))
        }
        for pid in chunk:
            posting = rows[pid]
            direct = direct_fields(posting.source, posting.raw_payload, posting.locations)
            wanted = wanted_fields(direct)
            if not wanted:
                continue  # the ATS stated everything: enrich saves it without a reader
            batch.append(build_posting(posting, wanted, values))
            if len(batch) >= limit:
                break
        if len(batch) >= limit:
            break
    pairs: list[dict[str, Any]] = []
    if shard[0] == 1:  # one shard carries the pairs, so parallel readers don't repeat them
        for dup in (await open_pairs(session))[:limit]:
            both = await pair_postings(session, dup)
            if both is not None:
                pairs.append(build_pair(dup, *both, values))
    out = build_export(batch, len(heads), run_active, values, pairs)
    out["unread_in_my_roles"] = len(mine)
    out["unread_in_shard"] = len(order)
    out["shard"] = f"{shard[0]}/{shard[1]}"
    return out


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


async def import_pair(session: AsyncSession, key: str, given: Any) -> str:
    """One Possible duplicate decided by the reader (R8): merged, kept apart, or not saved."""
    try:
        dup_id = uuid.UUID(key.removeprefix(PAIR))
        answer = SameJob.model_validate(given)
    except (ValueError, ValidationError):
        return f"{key}: not saved (needs a pair id and {{same: true or false, reason}})"
    dup = await session.get(PossibleDuplicate, dup_id)
    if dup is None or dup.decision != "open" or await pair_postings(session, dup) is None:
        await session.rollback()
        return f"{key}: not saved (pair already decided or gone)"
    decision = await apply_same(session, dup, answer)
    return f"{key}: {decision.replace('_', ' ')}"


async def import_fields(session: AsyncSession, replies: dict[str, Any]) -> list[str]:
    """Save each posting that passes; one line per posting, then a total."""
    if not isinstance(replies, dict):
        raise SystemExit("fields.json must be one JSON object keyed by posting id")
    out: list[str] = []
    listings: set[uuid.UUID] = set()
    for posting_id, given in replies.items():
        if posting_id.startswith(PAIR):
            out.append(await import_pair(session, posting_id, given))
            continue
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
    saved = sum(line.endswith((": saved", ": merged", ": kept apart")) for line in out)
    out.append(f"saved {saved} of {len(replies)}")
    return out


async def status(session: AsyncSession) -> str:
    """One line for the runner (AH4), so a shell script needs no JSON parser: whether a
    search is still running, how many postings are unread and how many pairs are open."""
    out = await export(session, limit=1)
    pairs = len(await open_pairs(session))
    run_active = int(bool(out["run_active"]))
    return f"run_active={run_active} unread={out['unread_total']} pairs={pairs}"


async def _main(argv: list[str]) -> None:
    command = argv[0] if argv else ""
    started = time.time()  # O17, level 1: each export and import is counted (agent_doors)
    async with get_sessionmaker()() as session:
        if command == "export":
            opts = dict(zip(argv[1::2], argv[2::2], strict=False))
            limit = max(1, int(opts.get("--limit") or BATCH))
            shard = parse_shard(opts.get("--shard") or "1/1")
            out = await export(session, limit, shard)
            text = json.dumps(out, ensure_ascii=False, indent=1)
            print(text)
            counts = agent_doors.reader_export(out, len(text.encode()))
            agent_doors.record("reader", "export", started, counts)
        elif command == "import":
            lines = await import_fields(session, json.loads(sys.stdin.read()))
            for line in lines:
                print(line)
            agent_doors.record("reader", "import", started, agent_doors.reader_import(lines))
        elif command == "status":
            print(await status(session))
        else:
            raise SystemExit(
                "usage: python -m pipeline.claude_reader "
                "export [--limit N] [--shard i/n] | import | status"
            )
    agent_doors.done()


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
