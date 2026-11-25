"""The AI writer's only door into JAP (PRD 1.22, W2 to W6; 1.30 AH1): export and import.

An outside AI harness (agent/run.sh starts it, PRD 1.30) answers the jobs waiting at Needs
input, in Review and (from PRD 1.29, V6) Automatic mode. A job its checker flags comes back
with "_review": "<reason>"; that reason is kept as the application's hold_reason, so the job
stops at Ready for review and Automatic mode never sends it. It never reads the database.
`export` gives it what writing needs (each job's frozen description and open questions, the
active resume, the answer bank), with my name, email, phone, address and profile links
swapped for [NAME], [EMAIL], [PHONE], [ADDRESS] and [LINK_n] by the same redaction every model
call uses, and guard() refuses the export if anything personal is left. `import` takes its
answers back, puts the real values in locally (restore), and hands them to the same answer
step as the Needs input panel, so the job is prepared again and reaches Ready for review.
Nothing here approves or submits.

With JAP_WRITER=agent (PRD 1.28; named claude before 1.30) it also writes required cover
letters (W11: a question marked cover_letter, answered with the letter under
cover_letter_rules; JAP builds the PDF) and reads new resumes (W12: each entry in resumes,
answered under "resume:<id>" with its skills, titles and years_experience, saved as suggestions
as JAP's own parse saves them).

From 1.37 (W13) a job the writer left out is not handed out again while what it would be
given is the same: the resume, the answer bank, the job's open questions and description.
The runner adds "_handed_out": [<application ids of the export>] to the answers it imports;
each of those the answers leave out is noted (writer_left_out_at and a hash of those inputs),
and the export skips it until one of them changes, or for 7 days at most. Its open questions
then say the writer left it out, so the Needs you panel no longer says it waits for the
writer. status counts such jobs as waiting_on_you.

    python -m pipeline.claude_writer export            > jobs.json
    python -m pipeline.claude_writer import            < answers.json
    python -m pipeline.claude_writer status            (one line for agent/run.sh)

answers.json: {"<application id>": {"<question key>": "<answer>", ...}, ...,
               "resume:<resume id>": {"skills": [...], "titles": [...], "years_experience": 8}}
"""

import asyncio
import hashlib
import json
import re
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AnswerBank, Application, Company, Listing, Profile, ResumeVersion
from database.session import get_sessionmaker
from llm.call import load_prompt
from llm.redact import Sensitive, find_links, from_resume_text, guard, redact, restore
from pipeline import agent_doors
from pipeline.agent_contract import CONTRACT_VERSION
from pipeline.apply.flow import letter_fields
from pipeline.resume import save_parsed, sensitive_from_profile
from schemas.resume import ParsedResume

RESUME = "resume:"
REVIEW = "_review"  # V6: the checker's reason a job must wait for review, in its answers
HANDED_OUT = "_handed_out"  # W13: the jobs the runner handed out, so the left out are noted
LEFT_OUT_FOR = timedelta(days=7)
LEFT_OUT = (
    "The AI writer left this job out: it may need a fact only you know (see the answer bank) "
    "or something your resume does not show"
)

# Said in every export, so the session knows how to hold a job back (V6, PRD 1.29).
REVIEW_RULE = (
    "If your checker finds anything in a job's answers or letter (a claim not in the resume "
    "or answer bank, a required field left empty or invalid), or the form or job description "
    'bans AI-written answers, still return its answers but add "_review": "<the reason, one '
    'sentence>" to that job. JAP then holds it for review and never sends it on its '
    "own. Leave a job out entirely only when you cannot answer it."
)


def _rules(purpose: str) -> str:
    """The rule lines of JAP's own prompt, so both writers write alike (not its JSON format)."""
    lines = load_prompt(purpose).system.splitlines()
    return "\n".join(line for line in lines if line.startswith("- "))


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
    jobs: list[dict[str, Any]],
    resume: str,
    bank: list[tuple[str, str]],
    values: Sensitive,
    resumes: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """The redacted payload; raises PersonalDataBlocked if any personal value survives."""
    out = {
        "contract_version": CONTRACT_VERSION,
        "review_rule": REVIEW_RULE,
        "cover_letter_rules": _rules("cover_letter"),
        "resume_rules": _rules("resume_parse"),
        "resumes": [
            {"resume_id": rid, "text": _clean(text, values)} for rid, text in resumes or []
        ],
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
                        "cover_letter": bool(q.get("cover_letter")),
                    }
                    for q in j["questions"]
                ],
            }
            for j in jobs
        ],
    }
    guard(json.dumps(out, ensure_ascii=False), values)
    return out


def inputs_of(resume: str, bank: list[tuple[str, str]], job: dict[str, Any]) -> str:
    """W13: a hash of what the writer is given for one job, to tell whether it changed."""
    asked = [(q["key"], str(q["label"]), list(q.get("options") or [])) for q in job["questions"]]
    given = [resume, sorted(bank), job["job_description"], asked]
    return hashlib.sha256(json.dumps(given, ensure_ascii=False).encode()).hexdigest()


async def _inputs(session: AsyncSession) -> tuple[str, list[tuple[str, str]]]:
    resume = await session.scalar(select(ResumeVersion.text).where(ResumeVersion.is_active))
    bank = [
        (b.question_pattern, b.answer)
        for b in await session.scalars(select(AnswerBank))
        if not CONTACT_QUESTION.search(b.question_pattern)
    ]
    return resume or "", bank


def left_out_unchanged(app: Application, inputs: str, now: datetime) -> bool:
    at = app.writer_left_out_at
    return app.writer_left_out_inputs == inputs and at is not None and now - at < LEFT_OUT_FOR


async def export(session: AsyncSession) -> dict[str, Any]:
    values = await sensitive(session)
    resume, bank = await _inputs(session)
    jobs, skipped = await waiting_jobs(session, resume, bank)
    # W12: resumes whose text is out but not read yet (no suggestions, no error).
    unread = await session.execute(
        select(ResumeVersion.id, ResumeVersion.text, ResumeVersion.uploaded_at)
        .where(
            ResumeVersion.text.is_not(None),
            ResumeVersion.parsed.is_(None),
            ResumeVersion.parse_error.is_(None),
        )
        .order_by(ResumeVersion.uploaded_at)
    )
    resumes = []
    for rid, text, _ in unread.all():
        if text:
            values = values.merge(from_resume_text(text))
            resumes.append((str(rid), text))
    out = build_export([j for _, j, _ in jobs], resume, bank, values, resumes)
    out["left_out_unchanged"] = len(skipped)
    return out


async def waiting_jobs(
    session: AsyncSession, resume: str, bank: list[tuple[str, str]], *, every: bool = False
) -> tuple[list[tuple[Application, dict[str, Any], str]], list[Application]]:
    """The jobs at Needs you the writer is to answer, each with the hash of its inputs (W13),
    and those skipped because it left them out on the same inputs. every=True skips none."""
    now = datetime.now(UTC)
    jobs: list[tuple[Application, dict[str, Any], str]] = []
    skipped: list[Application] = []
    rows = await session.execute(
        select(Application, Listing, Company)
        .join(Listing, Listing.id == Application.listing_id)
        .join(Company, Company.id == Listing.company_id)
        .where(Application.status == "needs_input")  # both modes (V6, PRD 1.29)
        .order_by(Application.created_at)
    )
    for app, listing, company in rows.all():
        limits = {f["key"]: f.get("max_chars") for f in app.form_fields or []}
        letters = {f["key"] for f in letter_fields(app)}
        open_ = [
            {
                **q,
                "max_chars": limits.get(q["key"]),
                "cover_letter": q.get("cover_letter") or q["key"] in letters,
            }  # fmt: skip
            for q in app.pending_questions or []
            if not q.get("blocked")
        ]
        if not open_:
            continue  # a file JAP doesn't have or a CAPTCHA: only I can deal with it
        jd = app.jd_snapshot or {}
        job = {
            "application_id": str(app.id),
            "company": company.name,
            "title": str(jd.get("title") or listing.title_display),
            "job_description": str(jd.get("text") or ""),
            "questions": open_,
        }
        inputs = inputs_of(resume, bank, job)
        if not every and left_out_unchanged(app, inputs, now):
            skipped.append(app)
        else:
            jobs.append((app, job, inputs))
    return jobs, skipped


async def note_left_out(session: AsyncSession, ids: set[str]) -> int:
    """W13: the jobs handed out and left out by the answers, still at Needs you, are noted
    with the hash of what the writer was given; how many."""
    resume, bank = await _inputs(session)
    jobs, _ = await waiting_jobs(session, resume, bank, every=True)
    now, noted = datetime.now(UTC), 0
    for app, _, inputs in jobs:
        if str(app.id) in ids:
            app.writer_left_out_at, app.writer_left_out_inputs = now, inputs
            app.pending_questions = [
                {**q, "reason": LEFT_OUT} if q.get("reason") == "Waiting for the AI writer" else q
                for q in app.pending_questions or []
            ]
            noted += 1
    await session.commit()
    return noted


def restore_answers(given: dict[str, str], values: Sensitive) -> dict[str, str]:
    """Real values back in; a placeholder with no value (a bare [LINK]) is dropped."""
    links = {f"[LINK_{i}]": url for i, url in enumerate(values.links, start=1)}
    return {k: restore(str(v), values, links).strip() for k, v in given.items()}


async def import_resume(session: AsyncSession, key: str, given: Any) -> str:
    """W12: one resume read by the writer, saved as suggestions; or why it was not."""
    try:
        version = await session.get(ResumeVersion, uuid.UUID(key.removeprefix(RESUME)))
        parsed = ParsedResume.model_validate(given)
    except (ValueError, ValidationError):
        return f"{key}: not read (needs a resume id and skills, titles, years_experience)"
    if version is None or not version.text or version.parsed is not None:
        return f"{key}: not read (already read, or gone)"
    skills = await save_parsed(session, version.id, parsed, version.text)
    return f"{key}: read ({len(skills)} skills)"


async def import_answers(session: AsyncSession, answers: dict[str, Any]) -> list[str]:
    """Answer each job through the Needs input step; one line per job saying what happened."""
    from api.applications import answer  # the same step as the Needs input panel
    from api.errors import ApiError
    from schemas.application import AnswerQuestionsIn

    values = await sensitive(session)
    handed = answers.pop(HANDED_OUT, None)
    handed = {str(i) for i in handed} if isinstance(handed, list) else set()
    out = []
    for app_id, given in answers.items():
        if app_id.startswith(RESUME):
            out.append(await import_resume(session, app_id, given))
            continue
        try:
            given = dict(given)
            held = str(given.pop(REVIEW, "") or "").strip()
            if held:  # V6: set before answering, so the job settles at Ready for review
                app = await session.get(Application, uuid.UUID(app_id), with_for_update=True)
                if app is not None:
                    app.hold_reason = f"Checker: {held}"[:500]
                    await session.commit()
            body = AnswerQuestionsIn(answers=restore_answers(given, values), save_to_bank=False)
            done = await answer(uuid.UUID(app_id), body, session)
            out.append(f"{app_id}: {done.status}")
        except (ApiError, ValueError) as exc:
            await session.rollback()
            out.append(f"{app_id}: not answered ({getattr(exc, 'detail', exc)})")
    left = await note_left_out(session, handed - set(answers))
    if left:
        out.append(
            f"left out: {left} job{'' if left == 1 else 's'} not handed out again until the "
            "answer bank, the resume or its questions change (7 days at most)"
        )
    return out


async def _main(command: str) -> None:
    started = time.time()  # O17, level 1: each export and import is counted (agent_doors)
    async with get_sessionmaker()() as session:
        if command == "export":
            out = await export(session)
            text = json.dumps(out, ensure_ascii=False, indent=1)
            print(text)
            counts = agent_doors.writer_export(out, len(text.encode()))
            agent_doors.record("writer", "export", started, counts)
        elif command == "import":
            answers = json.loads(sys.stdin.read())
            lines = await import_answers(session, answers)
            for line in lines:
                print(line)
            counts = agent_doors.writer_import(answers, lines)
            agent_doors.record("writer", "import", started, counts)
        elif command == "status":
            out = await export(session)
            print(
                f"jobs={len(out['jobs'])} resumes={len(out['resumes'])}"
                f" waiting_on_you={out['left_out_unchanged']}"
            )
        else:
            raise SystemExit("usage: python -m pipeline.claude_writer export|import|status")
    agent_doors.done()


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else ""))
