"""The database steps of apply-listing (design doc: apply-listing, step by step).

Each function is one Inngest step and returns ids, statuses or times only, never answers
or resume text (Inngest stores step output). The application row is the record: the API
changes it (Approve, an answer, Skip) and then sends an event, and apply-listing reads the
row when it wakes, so a press that lands before the function starts waiting is not lost.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import (
    Application,
    Company,
    Listing,
    Posting,
    Profile,
    ResumeVersion,
    Run,
    WatchedBoard,
)
from llm import PersonalDataBlocked, SpendCapReached
from llm.client import ModelCallError
from pipeline.apply import fields as rules
from pipeline.apply.answers import bank_answer, draft_answer, pick_option
from pipeline.apply.form import FormError, FormField, FormRead, Upload, read_form, submit_form
from pipeline.cover_letter import LetterFailed, generate, upload_name, write_pdf
from pipeline.resume import sensitive_from_profile
from pipeline.runs import in_scope, role_matcher

ACTIVE = ("preparing", "ready_for_review", "applying", "needs_input")
CHOICE = ("select", "radio", "checkbox", "checkboxes")

log = logging.getLogger(__name__)


class Stop(Exception):
    """Nothing to do for this listing (already applied, skipped, closed or busy)."""


async def _listing_status(session: AsyncSession, listing_id: uuid.UUID, status: str) -> None:
    await session.execute(update(Listing).where(Listing.id == listing_id).values(status=status))


async def _app(session: AsyncSession, app_id: str, *, lock: bool = False) -> Application:
    app = await session.get(Application, uuid.UUID(app_id), with_for_update=lock)
    if app is None:
        raise Stop(f"application {app_id} not found")
    return app


async def why_not(session: AsyncSession, listing_id: uuid.UUID) -> str | None:
    """None when the listing may be prepared now, else the reason it may not."""
    listing = await session.get(Listing, listing_id)
    if listing is None:
        return "No such listing"
    if listing.status in ("applied", "skipped", "closed"):
        return f"The job is {listing.status}"
    applied = await session.scalar(
        select(Application.id).where(
            Application.listing_id == listing_id, Application.status == "applied"
        )
    )
    if applied is not None:  # one company and role is one listing, reposts included
        return "You already applied for this job"
    busy = await session.scalar(
        select(Application.id).where(
            Application.listing_id == listing_id, Application.status.in_(ACTIVE)
        )
    )
    if busy is not None:
        return "This job is already being prepared"
    primary = (
        await session.get(Posting, listing.primary_posting_id)
        if listing.primary_posting_id
        else None
    )
    if primary is None or primary.state != "open":
        return "The posting is closed"
    resume = await session.scalar(select(ResumeVersion.id).where(ResumeVersion.is_active))
    if resume is None:
        return "Upload a resume in My profile first"
    return None


async def create_attempt(session: AsyncSession, listing_id: str, run_id: str | None) -> str:
    """check and create-attempt: the new application's id, or Stop."""
    lid = uuid.UUID(listing_id)
    reason = await why_not(session, lid)
    if reason:
        raise Stop(reason)
    listing = await session.get(Listing, lid)
    profile = await session.scalar(select(Profile))
    resume = await session.scalar(select(ResumeVersion).where(ResumeVersion.is_active))
    assert listing is not None and listing.primary_posting_id and resume is not None
    app = Application(
        listing_id=lid,
        posting_id=listing.primary_posting_id,
        run_id=uuid.UUID(run_id) if run_id else None,
        mode=profile.apply_mode if profile else "review",
        resume_version_id=resume.id,
        attempts=1,
    )
    session.add(app)
    listing.status = "applying"
    try:
        await session.commit()
    except IntegrityError as exc:  # one_active_attempt: another attempt got there first
        await session.rollback()
        raise Stop("This job is already being prepared") from exc
    return str(app.id)


async def snapshot(session: AsyncSession, app_id: str) -> str:
    """Freeze the JD (G5) and return the form's URL."""
    app = await _app(session, app_id)
    posting = await session.get(Posting, app.posting_id)
    assert posting is not None
    app.jd_snapshot = {
        "title": posting.title_raw,
        "text": posting.description_text,
        "html": posting.description_html,
        "posting_url": posting.posting_url,
        "apply_url": posting.apply_url,
        "source": posting.source,
        "locations": posting.locations,
    }
    app.captured_at = datetime.now(UTC)
    await session.commit()
    return posting.apply_url


def form_of(app: Application) -> list[FormField]:
    return [
        FormField(
            key=f["key"],
            label=f["label"],
            type=f["type"],
            required=bool(f["required"]),
            options=list(f.get("options") or []),
            max_chars=f.get("max_chars"),
        )
        for f in app.form_fields or []
    ]


async def save_form(session: AsyncSession, app_id: str, read: FormRead) -> None:
    app = await _app(session, app_id)
    app.form_fields = [{**f.as_dict(), "filled_from": ""} for f in read.fields]
    app.answers = {}
    if read.captcha:
        app.pending_questions = [
            {"key": "captcha", "label": "CAPTCHA", "reason": "The form shows a CAPTCHA, which JAP "
             "does not solve. Open the posting and apply by hand, or Skip.", "blocked": True}
        ]  # fmt: skip
    await session.commit()


@dataclass
class Mapped:
    # ready_for_review, needs_input, applying (Automatic mode), or cover_letter: a required
    # cover letter field has no letter yet, so the cover-letter step runs next
    status: str
    drafted: int
    pending: int


async def map_fields(session: AsyncSession, app_id: str) -> Mapped:
    """map-fields and draft-answers. Contact fields come from My profile by code, the
    resume file from the active resume, other questions from the answer bank; a required
    free-text question with no stored answer is drafted. A fact only I know, a required
    choice with no saved answer, or a file JAP doesn't have makes it Needs input."""
    app = await _app(session, app_id)
    if app.status not in ("preparing",):
        raise Stop(f"application is {app.status}")
    profile = await session.scalar(select(Profile))
    listing = await session.get(Listing, app.listing_id)
    resume = await session.get(ResumeVersion, app.resume_version_id)
    assert listing is not None and resume is not None
    company = await session.get(Company, listing.company_id)
    jd = app.jd_snapshot or {}
    sensitive = sensitive_from_profile(profile)
    captcha = [q for q in app.pending_questions or [] if q.get("key") == "captcha"]

    old = {f["key"]: f for f in app.form_fields or []}
    answers: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    drafted = 0
    draft_failed = 0
    need_letter = False
    for f in form_of(app):
        prev = old.get(f.key, {})
        entry: dict[str, Any] = {**f.as_dict(), "filled_from": ""}
        value: str | None = None
        source = ""
        if rules.is_cover_letter_field(f):
            # Only a mandatory field gets a letter (C1); an optional one stays empty.
            if f.required and app.cover_letter_text:
                value = letter_value(f, app, company.name if company else "", profile)
                source = "you" if prev.get("filled_from") == "you" else "cover_letter"
            elif f.required:
                need_letter = True
        elif prev.get("filled_from") == "you" and (app.answers or {}).get(f.key):
            value, source = str((app.answers or {})[f.key]), "you"
        elif contact := (rules.contact_value(f, profile) if profile else None):
            source, value = f"profile:{contact[0]}", contact[1]
        elif rules.is_resume_field(f):
            value, source = resume.original_name, "resume"
        elif f.type == "file":
            if f.required:
                pending.append({"key": f.key, "label": f.label, "blocked": True,
                                "reason": "The form needs a file JAP doesn't have"})  # fmt: skip
        elif (saved := await bank_answer(session, f.label)) is not None:
            choice = f.type in CHOICE and bool(f.options)
            picked = pick_option(saved.answer, f.options) if choice else None
            if choice and picked is None and f.type != "checkbox":
                if f.required:
                    pending.append({"key": f.key, "label": f.label, "options": f.options,
                                    "reason": f'Your saved answer "{saved.answer}" is not one of '
                                    "the choices"})  # fmt: skip
            else:
                value, source = (picked or saved.answer), "answer_bank"
                saved.last_used_at = datetime.now(UTC)
        elif not f.required:
            pass  # optional fields only when an answer already exists
        elif rules.needs_my_fact(f):
            pending.append({"key": f.key, "label": f.label, "options": f.options,
                            "reason": "Only you know this"})  # fmt: skip
        elif f.type in CHOICE:
            pending.append({"key": f.key, "label": f.label, "options": f.options,
                            "reason": "A required choice with no saved answer"})  # fmt: skip
        elif rules.is_free_text(f):
            try:
                draft = await draft_answer(
                    session,
                    label=f.label,
                    max_chars=f.max_chars,
                    company=company.name if company else "",
                    title=str(jd.get("title") or listing.title_display),
                    jd_text=str(jd.get("text") or ""),
                    resume_text=resume.text or "",
                    profile=profile,
                    sensitive=sensitive,
                )
            except (ModelCallError, SpendCapReached, PersonalDataBlocked) as exc:
                log.warning("draft failed for %s (%s): %s", app.id, f.label, exc)
                pending.append({"key": f.key, "label": f.label, "draft_failed": True,
                                "reason": f"JAP couldn't draft an answer: {exc}"})  # fmt: skip
                draft_failed += 1
            else:
                if draft.answer is None:
                    reason = f"Only you know this ({draft.missing_fact})"
                    pending.append({"key": f.key, "label": f.label, "reason": reason})
                else:
                    value, source = draft.answer, "drafted"
                    drafted += 1
        else:  # a required number or date with nothing stored
            pending.append({"key": f.key, "label": f.label, "reason": "Only you know this"})
        if value:
            answers[f.key] = value
            entry["filled_from"] = source
        out.append(entry)

    pending = captcha + pending
    if draft_failed and app.run_id:
        await bump(session, app.run_id, "draft_failed", draft_failed)
    app.form_fields = out
    app.answers = answers
    if need_letter:  # the cover-letter step writes it, then settles the status
        app.pending_questions = pending or None
        await session.commit()
        return Mapped(status="cover_letter", drafted=drafted, pending=len(pending))
    await settle(session, app, pending)
    return Mapped(status=app.status, drafted=drafted, pending=len(pending))


async def unclear(session: AsyncSession, app: Application) -> str | None:
    """Why Automatic mode must not submit this job on its own (A3), or None. A job whose
    work mode the posting doesn't state (Work mode unclear) waits for me."""
    listing = await session.get(Listing, app.listing_id)
    if listing is not None and listing.work_mode is None:
        return "Work mode unclear: the posting doesn't say remote, hybrid or onsite"
    return None


async def settle(session: AsyncSession, app: Application, pending: list[dict[str, Any]]) -> None:
    """Needs input when anything is pending, else Ready for review, or applying in
    Automatic mode when nothing about the job is unclear (A3)."""
    app.pending_questions = pending or None
    app.review_reason = await unclear(session, app) if app.mode == "automatic" else None
    if pending:
        app.status = "needs_input"
    elif app.mode == "automatic" and app.review_reason is None:
        app.status = "applying"
    else:
        app.status = "ready_for_review"
    await _listing_status(session, app.listing_id, app.status)
    if app.status == "needs_input" and app.run_id:
        await bump(session, app.run_id, "needs_input")
    elif app.status == "ready_for_review" and app.run_id:
        await bump(session, app.run_id, "ready_for_review")
    await session.commit()


def letter_fields(app: Application) -> list[dict[str, Any]]:
    return [f for f in app.form_fields or []
            if f["required"] and rules.is_cover_letter_field(FormField(
                key=f["key"], label=f["label"], type=f["type"], required=True))]  # fmt: skip


def letter_value(f: FormField, app: Application, company: str, profile: Profile | None) -> str:
    """What a cover letter field holds: the text itself, or the PDF's file name."""
    if f.type == "file":
        return upload_name(profile.full_name if profile else None, company)
    return app.cover_letter_text or ""


async def set_letter(session: AsyncSession, app: Application, text: str, *, source: str) -> None:
    """Keep a letter on the application (written by JAP, or mine): its text, the PDF built
    from it, and every cover letter field filled with it. The caller commits."""
    listing = await session.get(Listing, app.listing_id)
    assert listing is not None
    company = await session.get(Company, listing.company_id)
    profile = await session.scalar(select(Profile))
    company_name = company.name if company else ""
    title = str((app.jd_snapshot or {}).get("title") or listing.title_display)
    app.cover_letter_text = text.strip()
    contact = " · ".join(v for v in [profile.email, profile.phone] if v) if profile else ""
    path = await write_pdf(
        data_path("cover_letters", f"{app.id}.pdf"),
        app.cover_letter_text,
        name=(profile.full_name if profile else None) or "",
        contact=contact,
        company=company_name,
        title=title,
    )
    app.cover_letter_path = str(path)
    answers = dict(app.answers or {})
    out = []
    keys = {f["key"] for f in letter_fields(app)}
    for f in app.form_fields or []:
        if f["key"] in keys:
            ff = FormField(key=f["key"], label=f["label"], type=f["type"], required=True)
            answers[f["key"]] = letter_value(ff, app, company_name, profile)
            f = {**f, "filled_from": source}
        out.append(f)
    app.form_fields = out
    app.answers = answers


async def write_cover_letter(session: AsyncSession, app_id: str) -> dict[str, Any]:
    """cover-letter (C1 to C6): write the letter for the mandatory field, check its claims
    against the resume, regenerate once, then settle the status. Two failures add a Needs
    input question instead, so nothing is sent without the required letter. Returns what
    the Inngest UI may show: the redacted prompt's start, never the letter or my name."""
    app = await _app(session, app_id)
    if app.status != "preparing":
        raise Stop(f"application is {app.status}")
    fields = letter_fields(app)
    pending = list(app.pending_questions or [])
    if not fields or app.cover_letter_text:
        await settle(session, app, pending)
        return {"status": app.status, "letter": "not needed"}
    profile = await session.scalar(select(Profile))
    listing = await session.get(Listing, app.listing_id)
    resume = await session.get(ResumeVersion, app.resume_version_id)
    assert listing is not None and resume is not None
    company = await session.get(Company, listing.company_id)
    jd = app.jd_snapshot or {}
    try:
        letter = await generate(
            session,
            company=company.name if company else "",
            title=str(jd.get("title") or listing.title_display),
            role=listing.canonical_role,
            jd_text=str(jd.get("text") or ""),
            resume_text=resume.text or "",
            tone_note=profile.tone_note if profile else None,
            sensitive=sensitive_from_profile(profile),
        )
    except LetterFailed as exc:
        app = await _app(session, app_id)
        f = fields[0]
        if str(exc).startswith("Spend cap reached"):  # no model call was made (Phase 8)
            reason = (
                f"{exc}, so JAP wrote no cover letter, and nothing is sent without one. "
                "Paste your own letter here, raise the cap on My profile, or Skip."
            )
        else:
            reason = (
                "JAP could not write a cover letter that passed the resume check twice "
                f"({exc}). Paste your own letter here, or Skip."
            )
        pending.append({"key": f["key"], "label": f["label"], "reason": reason})
        await settle(session, app, pending)
        return {"status": app.status, "letter": "failed twice", "error": str(exc)[:500]}
    app = await _app(session, app_id)
    app.claim_check = letter.claims
    await set_letter(session, app, letter.text, source="cover_letter")
    await settle(session, app, pending)
    return {
        "status": app.status,
        "letter": "written",
        "attempts": letter.attempts,
        "words": len(letter.text.split()),
        "claims_checked": len(letter.claims),
        "format": "pdf" if any(f["type"] == "file" for f in fields) else "text",
        "prompt_sent_to_model": letter.prompt_excerpt,
        "placeholders_in_prompt": letter.placeholders,
        "name_filled_in_locally": "[NAME]" not in letter.text,
    }


async def status_of(session: AsyncSession, app_id: str) -> str:
    return (await _app(session, app_id)).status


async def bump(session: AsyncSession, run_id: uuid.UUID, key: str, by: int = 1) -> None:
    """Add to a run's count (applied, failed, needs input, answers not drafted), as each
    attempt ends."""
    run = await session.get(Run, run_id, with_for_update=True)
    if run is not None:
        counts = dict(run.counts or {})
        counts[key] = int(counts.get(key) or 0) + by
        run.counts = counts


def local_midnight(now: datetime, tz: str) -> datetime:
    local = now.astimezone(ZoneInfo(tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


async def applied_today(session: AsyncSession, now: datetime) -> int:
    since = local_midnight(now, get_settings().jap_tz)
    n = await session.scalar(
        select(func.count())
        .select_from(Application)
        .where(Application.status == "applied", Application.submitted_at >= since)
    )
    return int(n or 0)


async def wait_before_submit(session: AsyncSession, now: datetime) -> datetime | None:
    """cap-check and the gap (A1): when submitting must wait until, or None to go now."""
    settings = get_settings()
    profile = await session.scalar(select(Profile))
    cap = profile.daily_cap if profile else 20
    if await applied_today(session, now) >= cap:
        return local_midnight(now, settings.jap_tz) + timedelta(days=1, minutes=1)
    last: datetime | None = await session.scalar(select(func.max(Application.submitted_at)))
    gap = timedelta(seconds=settings.jap_apply_gap_seconds)
    if last is not None and now - last < gap:
        return last + gap
    return None


def _file_letters(app: Application) -> list[str]:
    return [f["key"] for f in letter_fields(app) if f["type"] == "file"]


class SubmitRetry(Exception):
    """Submit failed before the button was pressed: safe to try again."""


def data_path(*parts: str) -> Path:
    return Path(get_settings().jap_data_dir).joinpath(*parts)


async def submit(session: AsyncSession, app_id: str) -> dict[str, Any]:
    """submit: {"wait_until": iso} when the cap or the gap says not yet, else
    {"status": "applied" | "failed"}. Submit is pressed at most once per attempt: a retry
    that finds submit_started_at set without a confirmation fails the attempt as "submit
    outcome unknown", so I check by hand."""
    now = datetime.now(UTC)
    app = await _app(session, app_id, lock=True)
    if app.status != "applying":
        raise Stop(f"application is {app.status}")
    until = await wait_before_submit(session, now)
    if until is not None:
        await session.rollback()
        return {"wait_until": until.isoformat()}
    if app.submit_started_at is not None:
        await fail(session, app, "Submit outcome unknown: check the employer's site by hand")
        return {"status": "failed"}
    app.submit_started_at = now
    fields = form_of(app)
    answers = {k: str(v) for k, v in (app.answers or {}).items()}
    resume = await session.get(ResumeVersion, app.resume_version_id)
    url = str((app.jd_snapshot or {}).get("apply_url") or "")
    await session.commit()
    assert resume is not None
    upload = Upload(Path(resume.file_path), resume.original_name, resume.mime)
    files = {f["key"]: upload for f in app.form_fields or [] if f.get("filled_from") == "resume"}
    for key in _file_letters(app) if app.cover_letter_path else []:
        assert app.cover_letter_path is not None
        name = answers.get(key) or "Cover-Letter.pdf"
        files[key] = Upload(Path(app.cover_letter_path), name, "application/pdf")
    shot = data_path("screenshots", f"{app_id}.png")
    trace = data_path("traces", f"{app_id}.zip")
    result = await submit_form(url, fields, answers, files, shot, trace)
    app = await _app(session, app_id, lock=True)
    if not result.clicked:
        app.submit_started_at = None
        await session.commit()
        raise SubmitRetry(result.error or "Could not fill the form")
    if not result.confirmed:
        app.screenshot_path = str(shot) if result.screenshot else None
        app.trace_path = str(trace) if trace.exists() else None
        await fail(session, app, result.error or "No confirmation after submit")
        return {"status": "failed"}
    submitted = datetime.now(UTC)
    app.status = "applied"
    app.submitted_at = submitted
    app.screenshot_path = str(shot) if result.screenshot else None
    app.trace_path = str(trace) if trace.exists() else None
    app.pending_questions = None
    app.error = None
    await session.execute(
        update(Listing)
        .where(Listing.id == app.listing_id)
        .values(status="applied", applied_at=submitted)
    )
    if app.run_id:
        await bump(session, app.run_id, "applied")
    await session.commit()
    return {"status": "applied"}


async def fail(session: AsyncSession, app: Application, error: str) -> None:
    if app.status in ("applied", "cancelled", "failed"):
        return
    app.status = "failed"
    app.error = error[:1000]
    await _listing_status(session, app.listing_id, "failed")
    if app.run_id:
        await bump(session, app.run_id, "failed")
    await session.commit()


async def fail_by_id(session: AsyncSession, app_id: str, error: str) -> None:
    app = await session.get(Application, uuid.UUID(app_id), with_for_update=True)
    if app is not None:
        await fail(session, app, error)


async def expire(session: AsyncSession, app_id: str, what: str) -> None:
    """A wait ran out (14 days): the attempt fails so the listing can be tried again."""
    await fail_by_id(session, app_id, f"No {what} within 14 days")


async def read_and_save(session: AsyncSession, app_id: str, url: str) -> int:
    """read-form: open the form with Playwright and keep its fields. A FormError keeps a
    screenshot of the page on the application (U15), then Inngest retries the step."""
    shot = data_path("screenshots", f"{app_id}.png")
    try:
        read = await read_form(url, shot)
    except FormError:
        if shot.exists():
            app = await _app(session, app_id)
            app.screenshot_path = str(shot)
            await session.commit()
        raise
    await save_form(session, app_id, read)
    return len(read.fields)


async def pick(session: AsyncSession, run_id: str) -> list[str]:
    """pick (Find and apply): listed, Not applied listings with an open primary posting,
    newest first, up to what is left of today's cap. A run scoped to some roles picks
    only the listings counted under those roles."""
    run = await session.get(Run, uuid.UUID(run_id))
    profile = await session.scalar(select(Profile))
    if run is None or run.mode != "find_and_apply":
        return []
    if await session.scalar(select(ResumeVersion.id).where(ResumeVersion.is_active)) is None:
        return []
    left = (profile.daily_cap if profile else 20) - await applied_today(session, datetime.now(UTC))
    if left <= 0:
        return []
    watched = (
        select(Posting.listing_id)
        .join(WatchedBoard, WatchedBoard.id == Posting.watched_board_id)
        .where(WatchedBoard.active)
    )
    rows = await session.scalars(
        select(Listing)
        .join(Posting, Posting.id == Listing.primary_posting_id)
        .where(
            Listing.matched.is_not(None),
            Listing.status == "not_applied",
            Listing.id.in_(watched),
            Posting.state == "open",
        )
        .order_by(Listing.last_posted_at.desc().nulls_last())
    )
    matcher = role_matcher((run.scope or {}).get("roles"))
    out: list[str] = []
    for listing in rows:
        if len(out) >= left:
            break
        if not in_scope(listing.title_display, matcher) or await why_not(session, listing.id):
            continue
        out.append(str(listing.id))
    return out
