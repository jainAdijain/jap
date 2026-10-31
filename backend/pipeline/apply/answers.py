"""Answers to form questions: the answer bank first, then a drafted answer (G4).

The bank is searched by the normalised question: an exact match, else pg_trgm similarity
of 0.8 or more. A required free-text question with no stored answer gets a draft tailored
to the JD and role. A drafted answer is shown in Review mode and is saved to the bank only
when I save it. When the writer model can't draft one, the question goes to Needs input
marked draft_failed, with the reason, and the run counts it.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AnswerBank, Profile
from llm import call
from llm.redact import Sensitive, restore

SIMILAR = 0.8
DEFAULT_MAX_CHARS = 1500
MAX_JD_CHARS = 12_000
MAX_RESUME_CHARS = 12_000


def normalize_question(q: str) -> str:
    """Lowercased, punctuation stripped, spaces collapsed (answer_bank.question_norm)."""
    return " ".join(re.sub(r"[^\w\s]", " ", q.lower()).split())


async def bank_answer(session: AsyncSession, question: str) -> AnswerBank | None:
    norm = normalize_question(question)
    if not norm:
        return None
    exact = await session.scalar(select(AnswerBank).where(AnswerBank.question_norm == norm))
    if exact is not None:
        return exact
    sim = func.similarity(AnswerBank.question_norm, norm)
    return await session.scalar(
        select(AnswerBank).where(sim >= SIMILAR).order_by(sim.desc()).limit(1)
    )


async def save_to_bank(session: AsyncSession, question: str, answer: str) -> None:
    """Add the answer, or replace the one stored for the same question."""
    norm = normalize_question(question)
    row = await session.scalar(select(AnswerBank).where(AnswerBank.question_norm == norm))
    if row is None:
        session.add(
            AnswerBank(
                question_pattern=question.strip(),
                question_norm=norm,
                answer=answer.strip(),
                last_used_at=datetime.now(UTC),
            )
        )
    else:
        row.answer = answer.strip()
        row.last_used_at = datetime.now(UTC)


def pick_option(answer: str, options: list[str]) -> str | None:
    """The option a stored answer stands for (case and spacing ignored)."""
    want = " ".join(answer.lower().split())
    for o in options:
        if " ".join(o.lower().split()) == want:
            return o
    for o in options:  # "Yes" answers "Yes, I am"
        if " ".join(o.lower().split()).startswith(want):
            return o
    return None


class DraftOut(BaseModel):
    answer: str
    needs_my_fact: bool
    missing_fact: str


@dataclass
class Draft:
    answer: str | None  # None: the question needs a fact only I know
    missing_fact: str | None


async def draft_answer(
    session: AsyncSession,
    *,
    label: str,
    max_chars: int | None,
    company: str,
    title: str,
    jd_text: str,
    resume_text: str,
    profile: Profile | None,
    sensitive: Sensitive,
) -> Draft:
    limit = max_chars or DEFAULT_MAX_CHARS
    result = await call(
        session,
        "answer",
        {
            "label": label,
            "max_chars": str(limit),
            "company": company,
            "title": title,
            "tone_note": (profile.tone_note if profile else None) or "none",
            "frozen_jd_text": jd_text[:MAX_JD_CHARS],
            "resume_text": resume_text[:MAX_RESUME_CHARS],
        },
        DraftOut,
        sensitive=sensitive,
        slot="write",
        temperature=0.3,
        generic_redact=frozenset({"resume_text", "frozen_jd_text"}),
        version="v2",
        # About 3 characters a token, plus room for the JSON around the answer.
        max_tokens=min(4_000, limit // 3 + 500),
    )
    out = result.output
    if out.needs_my_fact or not out.answer.strip():
        return Draft(answer=None, missing_fact=out.missing_fact or label)
    text = restore(out.answer, sensitive, result.links).strip()
    return Draft(answer=text[:limit], missing_fact=None)
