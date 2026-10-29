"""Failure alerts (Phase 8, PRD Hosting and operations): one email to the address on My
profile when a run fails or a board in it can't be fetched, when an application ends
Failed, and when the weekly form test finds a form JAP can no longer read.

Each alert is sent once: run.alert_sent_at and application.alert_sent_at are set after the
send, as their own step (functions/notify_failure.py), like the confirmation email. Alerts
never carry answers, letters or resume text, only what failed and why.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import Application, Company, FormCheck, Listing, Profile, Run
from pipeline.notify import EmailNotSent, Mail

TRIGGERS = {
    "manual": "the Run button",
    "scheduled": "the Daily run",
    "resume_updated": "a resume update",
    "profile_saved": "a profile change",
}


async def _to(session: AsyncSession) -> str:
    profile = await session.scalar(select(Profile))
    if not profile or not profile.email:
        raise EmailNotSent("My profile has no email address")
    return str(profile.email)


def _local(at: datetime | None) -> str:
    tz = get_settings().jap_tz
    return f"{(at or datetime.now(UTC)).astimezone(ZoneInfo(tz)):%d %b %Y, %H:%M} ({tz})"


def _ui(path: str = "") -> str:
    return f"{get_settings().jap_ui_url.rstrip('/')}/{path}"


async def run_alert(session: AsyncSession, run_id: uuid.UUID) -> Mail | None:
    """The alert for a run that failed or lost a board, or None (nothing failed, or sent)."""
    run = await session.get(Run, run_id)
    if run is None or run.alert_sent_at is not None:
        return None
    counts = run.counts or {}
    boards_failed = int(counts.get("boards_failed") or 0)
    if run.status != "failed" and not boards_failed:
        return None
    when = _local(run.ended_at or run.started_at)
    lines = [f"A JAP run from {TRIGGERS.get(run.trigger, run.trigger)} on {when} "]
    if run.status == "failed":
        subject = "JAP alert: a run failed"
        lines[0] += "failed."
        lines += ["", f"Reason: {run.error or 'unknown'}"]
    else:
        subject = f"JAP alert: {boards_failed} board{'s' if boards_failed != 1 else ''} failed"
        lines[0] += f"finished, but {boards_failed} of {counts.get('boards', '?')} boards failed."
        lines += ["", "Boards that failed:"]
        lines += [f"- {e}" for e in counts.get("board_errors") or []]
    lines += ["", f"Runs card in JAP: {_ui()}"]
    return Mail(to=await _to(session), subject=subject, body="\n".join(lines) + "\n")


async def application_alert(session: AsyncSession, app_id: uuid.UUID) -> Mail | None:
    """The alert for an application that ended Failed, or None (not failed, or sent)."""
    app = await session.get(Application, app_id)
    if app is None or app.status != "failed" or app.alert_sent_at is not None:
        return None
    listing = await session.get(Listing, app.listing_id)
    assert listing is not None
    company = await session.get(Company, listing.company_id)
    title = str((app.jd_snapshot or {}).get("title") or listing.title_display)
    company_name = company.name if company else ""
    shot = Path(app.screenshot_path) if app.screenshot_path else None
    lines = [
        f"JAP could not apply for {title} at {company_name}.",
        "",
        f"Reason: {app.error or 'unknown'}",
        f"Mode: {'Automatic' if app.mode == 'automatic' else 'Review'}",
        f"Failed at: {_local(app.updated_at)}",
        "",
        f"Open the job in JAP for the screenshot and Retry: {_ui(f'?listing={listing.id}')}",
    ]
    if shot is not None and shot.exists():
        lines += ["", "The page as JAP saw it is attached."]
    return Mail(
        to=await _to(session),
        subject=f"JAP alert: apply failed: {title} at {company_name}",
        body="\n".join(lines) + "\n",
        attachment=shot if shot is not None and shot.exists() else None,
        attachment_name="screenshot.png",
        attachment_type="image/png",
    )


async def form_check_alert(session: AsyncSession, check_ids: list[uuid.UUID]) -> Mail | None:
    """The alert for weekly form tests that failed, or None when every form read fine."""
    checks = [c for c in [await session.get(FormCheck, i) for i in check_ids] if c is not None]
    bad = [c for c in checks if not c.ok]
    if not bad:
        return None
    lines = ["The weekly form test could not read these application forms:", ""]
    lines += [f"- {c.ats.title()}: {c.error or 'unknown'} ({c.url or 'no posting'})" for c in bad]
    lines += [
        "",
        "The ATS may have changed its form. Jobs on it will fail until the reader is fixed.",
        f"Status card on the JAP Dashboard: {_ui()}",
    ]
    return Mail(
        to=await _to(session),
        subject=f"JAP alert: form test failed for {', '.join(c.ats.title() for c in bad)}",
        body="\n".join(lines) + "\n",
    )


async def mark_sent(session: AsyncSession, kind: str, target: uuid.UUID) -> None:
    row: Run | Application | None = (
        await session.get(Run, target) if kind == "run" else await session.get(Application, target)
    )
    if row is not None and row.alert_sent_at is None:
        row.alert_sent_at = datetime.now(UTC)
        await session.commit()
