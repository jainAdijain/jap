"""The Claude writer's only door into JAP (PRD 1.22, W2 to W6): export and import.

A scheduled Claude session answers the jobs waiting at Needs input in Review mode. It never
reads the database. `export` gives it what writing needs (each job's frozen description and
open questions, the active resume, the answer bank), with my name, email, phone, address and
profile links swapped for [NAME], [EMAIL], [PHONE], [ADDRESS] and [LINK_n] by the same
redaction every model call uses, and guard() refuses the export if anything personal is left.
`import` takes its answers back, puts the real values in locally (restore), and hands them to
the same answer step as the Needs input panel, so the job is prepared again and reaches Ready
for review. Nothing here approves or submits.

    python -m pipeline.claude_writer export            > jobs.json
    python -m pipeline.claude_writer import            < answers.json

answers.json: {"<application id>": {"<question key>": "<answer>", ...}, ...}
"""

import asyncio
import json
import re
import sys
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AnswerBank, Application, Company, Listing, Profile, ResumeVersion
from database.session import get_sessionmaker
from llm.redact import Sensitive, find_links, from_resume_text, guard, redact, restore
from pipeline.resume import sensitive_from_profile

# Saved answers about who I am or where my profiles are: JAP fills these by code, the writer
# never needs them, and a bare handle ("jdoe" for GitHub) is not something redaction can spot.
CONTACT_QUESTION = re.compile(
    r"\b(github|gitlab|linkedin|twitter|website|portfolio|profile|url|link|phone|mobile|"
    r"e-?mail|address|name)\b|\bx\b",
    re.IGNORECASE,
)


async def sensitive(session: AsyncSession) -> Sensitive:
    """Every personal value to keep out: My profile, the resume's own contact lines, and
    profile links saved in the answer bank (a GitHub answer). Links are sorted, so export
    and import number them the same way."""
    profile = await session.scalar(select(Profile))
    resume = await session.scalar(select(ResumeVersion).where(ResumeVersion.is_active))
    values = sensitive_from_profile(profile)
    if resume is not None and resume.text:
        values = values.merge(from_resume_text(resume.text))
    answers = await session.scalars(select(AnswerBank.answer))
    banked = [link for a in answers for link in find_links(a)]
    values.links = sorted(set(values.links + banked))
    return values


def _clean(text: str, values: Sensitive) -> str:
    return redact(text, values, generic=True).text


def build_export(
    jobs: list[dict[str, Any]], resume: str, bank: list[tuple[str, str]], values: Sensitive
) -> dict[str, Any]:
    """The redacted payload; raises PersonalDataBlocked if any personal value survives."""
    out = {
        "resume": _clean(resume, values),
        "answer_bank": [
            {"question": _clean(q, values), "answer": _clean(a, values)} for q, a in bank
        ],
        "jobs": [
            {
                "application_id": j["application_id"],
                "company": j["company"],
                "title": j["title"],
                "job_description": _clean(j["job_description"], values),
                "questions": [
                    {
                        "key": q["key"],
                        "label": _clean(str(q["label"]), values),
                        "options": list(q.get("options") or []),
                        "max_chars": q.get("max_chars"),
                        "why_open": str(q.get("reason") or ""),
                    }
                    for q in j["questions"]
                ],
            }
            for j in jobs
        ],
    }
    guard(json.dumps(out, ensure_ascii=False), values)
    return out


async def export(session: AsyncSession) -> dict[str, Any]:
    values = await sensitive(session)
    resume = await session.scalar(select(ResumeVersion.text).where(ResumeVersion.is_active))
    bank = [
        (b.question_pattern, b.answer)
        for b in await session.scalars(select(AnswerBank))
        if not CONTACT_QUESTION.search(b.question_pattern)
    ]
    rows = await session.execute(
        select(Application, Listing, Company)
        .join(Listing, Listing.id == Application.listing_id)
        .join(Company, Company.id == Listing.company_id)
        .where(Application.status == "needs_input", Application.mode == "review")
        .order_by(Application.created_at)
    )
    jobs = []
    for app, listing, company in rows.all():
        limits = {f["key"]: f.get("max_chars") for f in app.form_fields or []}
        open_ = [
            {**q, "max_chars": limits.get(q["key"])}
            for q in app.pending_questions or []
            if not q.get("blocked")
        ]
        if not open_:
            continue  # a file JAP doesn't have or a CAPTCHA: only I can deal with it
        jd = app.jd_snapshot or {}
        jobs.append(
            {
                "application_id": str(app.id),
                "company": company.name,
                "title": str(jd.get("title") or listing.title_display),
                "job_description": str(jd.get("text") or ""),
                "questions": open_,
            }
        )
    return build_export(jobs, resume or "", bank, values)


def restore_answers(given: dict[str, str], values: Sensitive) -> dict[str, str]:
    """Real values back in; a placeholder with no value (a bare [LINK]) is dropped."""
    links = {f"[LINK_{i}]": url for i, url in enumerate(values.links, start=1)}
    return {k: restore(str(v), values, links).strip() for k, v in given.items()}


async def import_answers(session: AsyncSession, answers: dict[str, dict[str, str]]) -> list[str]:
    """Answer each job through the Needs input step; one line per job saying what happened."""
    from api.applications import answer  # the same step as the Needs input panel
    from api.errors import ApiError
    from schemas.application import AnswerQuestionsIn

    values = await sensitive(session)
    out = []
    for app_id, given in answers.items():
        try:
            body = AnswerQuestionsIn(answers=restore_answers(given, values), save_to_bank=False)
            done = await answer(uuid.UUID(app_id), body, session)
            out.append(f"{app_id}: {done.status}")
        except (ApiError, ValueError) as exc:
            await session.rollback()
            out.append(f"{app_id}: not answered ({getattr(exc, 'detail', exc)})")
    return out


async def _main(command: str) -> None:
    async with get_sessionmaker()() as session:
        if command == "export":
            print(json.dumps(await export(session), ensure_ascii=False, indent=1))
        elif command == "import":
            for line in await import_answers(session, json.loads(sys.stdin.read())):
                print(line)
        else:
            raise SystemExit("usage: python -m pipeline.claude_writer export|import")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else ""))
