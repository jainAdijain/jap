"""Filling what the ATS did not give (I3, I4): model extraction and the dedup embedding.

Fields the ATS states directly are copied and never sent for extraction: Ashby salary, and
the work mode and a remote job's regions (sources/workplace.py: Ashby and Lever
workplaceType, Ashby isRemote, a "Remote" location entry). The rest is extracted once per
description: a posting whose extracted_hash equals its description_hash is skipped, so an
unchanged posting costs nothing on the next run. Who reads it is JAP_READER (PRD 1.25 R1):
OpenRouter here, or by default the scheduled Claude reader (pipeline/claude_reader.py),
which saves through the same save_extracted. The embedding is cached the same way and
costs nothing anyway (Ollama on the Mac).
"""

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import Posting, Profile, WatchedBoard
from llm import call
from pipeline.resume import normalize_skills, sensitive_from_profile, skill_aliases
from schemas.extract import FIELDS, Extracted, from_output, output_model
from sources import ashby, workplace

MAX_JD_CHARS = 12_000  # about 3,000 tokens; longer descriptions are cut, which caps cost
EMBED_CHARS = 6_000  # nomic-embed-text reads 2,048 tokens by default in Ollama


def direct_fields(
    source: str, raw: dict[str, Any], locations: list[str] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if source == "ashby":
        salary = ashby.salary(raw)
        if salary is not None:
            out["salary"] = salary.model_dump()
    out.update(workplace.stated(source, raw, locations or []))
    return out


@dataclass
class ExtractResult:
    skipped: bool
    fields: list[str]
    sent_to_model: str | None = None
    # JAP_READER=claude: left unread for the Claude reader (PRD 1.25), nothing sent anywhere.
    waiting_for_reader: bool = False


async def wanted_posting(session: AsyncSession, posting_id: uuid.UUID) -> Posting | None:
    """The posting, or None when it is not to be read: it is gone, closed, or its company
    is no longer followed (I13). A read queued before an unfollow ends here, unspent."""
    posting = await session.get(Posting, posting_id)
    if posting is None or posting.state != "open":
        return None
    board = await session.get(WatchedBoard, posting.watched_board_id)
    return posting if board is not None and board.active else None


def wanted_fields(direct: dict[str, Any]) -> list[str]:
    """The fields still to be read from the description: those the ATS did not state."""
    return [f for f in FIELDS if f not in direct]


async def save_extracted(
    session: AsyncSession,
    posting_id: uuid.UUID,
    description_hash: str,
    direct: dict[str, Any],
    read: dict[str, Any],
) -> bool:
    """Store what the ATS stated plus what was read (by OpenRouter or the Claude reader),
    and mark the posting read for this description (I4). Skills are normalised here, so both
    readers save alike. False, and nothing saved, when the description changed meanwhile."""
    values: dict[str, Any] = {**direct, **read}
    if "skills" in values:
        values["skills"] = normalize_skills(values["skills"], await skill_aliases(session))
    extracted = Extracted.model_validate({**values, "direct": sorted(direct)})
    posting = await session.get(Posting, posting_id)
    if posting is None or posting.description_hash != description_hash:
        return False
    posting.extracted = extracted.model_dump(exclude_none=True)
    posting.extracted_hash = description_hash
    await session.commit()
    return True


async def keep_direct(session: AsyncSession, posting: Posting, direct: dict[str, Any]) -> None:
    """Claude reader (PRD 1.25 R1): copy what the ATS states now, so a remote job matches on
    Location before it is read, but leave the posting unread (extracted_hash untouched) for
    make reader-export. Fields read from an earlier description stay until it is read again."""
    old = {k: v for k, v in (posting.extracted or {}).items() if k not in ("direct", *direct)}
    extracted = Extracted.model_validate({**old, **direct, "direct": sorted(direct)})
    posting.extracted = extracted.model_dump(exclude_none=True)
    await session.commit()


async def extract_posting(session: AsyncSession, posting_id: uuid.UUID) -> ExtractResult:
    posting = await wanted_posting(session, posting_id)
    if posting is None:
        return ExtractResult(skipped=True, fields=[])
    if posting.extracted_hash == posting.description_hash:
        return ExtractResult(skipped=True, fields=[])  # cached by description hash (I4)
    description_hash = posting.description_hash
    direct = direct_fields(posting.source, posting.raw_payload, posting.locations)
    wanted = wanted_fields(direct)
    sent: str | None = None
    read: dict[str, Any] = {}
    if wanted and posting.description_text.strip():
        if get_settings().jap_reader == "agent":
            await keep_direct(session, posting, direct)
            return ExtractResult(skipped=False, fields=[], waiting_for_reader=True)
        profile = await session.scalar(select(Profile))
        result = await call(
            session,
            "extract",
            {
                "fields": ", ".join(wanted),
                "title": posting.title_raw,
                "locations": "; ".join(posting.locations) or "not stated",
                "description_text": posting.description_text[:MAX_JD_CHARS],
            },
            output_model(wanted),
            sensitive=sensitive_from_profile(profile),
            generic_redact=frozenset({"description_text"}),
        )
        sent = result.model_input
        read = from_output(result.output)
    await save_extracted(session, posting_id, description_hash, direct, read)
    return ExtractResult(skipped=False, fields=wanted, sent_to_model=sent)


class EmbedFailed(Exception):
    pass


async def embed(texts: str) -> list[float]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/embed",
                json={"model": settings.jap_model_embed, "input": texts},
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise EmbedFailed(f"Ollama did not embed: {type(exc).__name__}") from exc
    vectors = resp.json().get("embeddings") or []
    if not vectors or len(vectors[0]) != settings.jap_embed_dim:
        raise EmbedFailed(f"Ollama returned no {settings.jap_embed_dim}-dimension vector")
    return [float(x) for x in vectors[0]]


async def embed_posting(session: AsyncSession, posting_id: uuid.UUID) -> bool:
    """Embed title and description for near-match dedup (Phase 3). False when cached."""
    posting = await wanted_posting(session, posting_id)
    if posting is None or posting.embedded_hash == posting.description_hash:
        return False
    description_hash = posting.description_hash
    # nomic-embed-text expects a task prefix.
    doc = f"search_document: {posting.title_raw}\n{posting.description_text[:EMBED_CHARS]}"
    vector = await embed(doc)
    await session.execute(
        text(
            "UPDATE posting SET embedding = CAST(:v AS vector), embedded_hash = :h "
            "WHERE id = :id AND description_hash = :h"
        ),
        {
            "v": "[" + ",".join(f"{x:.7g}" for x in vector) + "]",
            "h": description_hash,
            "id": posting_id,
        },
    )
    await session.commit()
    return True
