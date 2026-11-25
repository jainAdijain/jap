"""An application sent or failed, as a span of the trace (PRD 1.38, O26).

The traces of 1.33 and 1.34 cover what the AI read and wrote, and stop there: an application
that then failed on its form, or went out, left nothing in them. Failed applications were
only in JAP's Failed panel and on Insights, and nothing said how good the AI writer's
answers were. Two spans now, sent through observability.trace like every other:

- apply.failed: its job site, how it was applied, whether Submit was pressed, and why, as a
  code from a fixed list (form_403, no_form, no_confirmation and so on), never the error.
- apply.sent: how many answers it held, how many the AI writer gave, and how many of those I
  changed before it went (writer_answers, kept at the import, against what was sent). A
  writer whose answers I rarely change is writing well; that is the quality signal.

Counts, codes and JAP's ids only, as O16 asks. Nothing here may fail the application it
describes: each function swallows its own errors.
"""

import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Application
from observability import trace
from pipeline.apply import pacing

log = logging.getLogger(__name__)

# How an application's error starts or what it holds, and its code (trace.FAILURES).
CODES = (
    ("within 14 days", "expired"),  # first: "No confirmation email within 14 days"
    ("403", "form_403"),
    ("No application form found", "no_form"),
    ("did not confirm", "no_confirmation"),
    ("No confirmation", "no_confirmation"),
    ("Submit outcome unknown", "outcome_unknown"),
    ("No option", "option_missing"),
    ("Locator.", "fill_error"),
    ("Cannot type", "fill_error"),
    ("Sending is off", "sending_off"),
)


def failure_code(error: str | None) -> str:
    """Why an application failed, as one code of trace.FAILURES."""
    text = error or ""
    return next((code for words, code in CODES if words in text), "other")


def edited(app: Application) -> tuple[int, int]:
    """How many answers the AI writer gave this application, and how many of them were
    changed before it went."""
    given = app.writer_answers if isinstance(app.writer_answers, dict) else {}
    sent = app.answers or {}
    changed = sum(str(sent.get(k, "")).strip() != str(v).strip() for k, v in given.items())
    return len(given), changed


async def _fields(session: AsyncSession, app: Application) -> dict[str, Any]:
    ats = await pacing.ats_of(session, app)
    return {
        "ats": ats if ats in trace.ATS else "other",
        "apply_mode": app.mode,
        "application_id": app.id,
        "posting_id": app.posting_id,
        "run_id": app.run_id,
        "attempt": app.attempts,
        "pressed": int(app.submit_started_at is not None),
        "held": int(bool(app.hold_reason)),
        "answers": len(app.answers or {}),
    }


def _began(app: Application) -> float:
    created = getattr(app, "created_at", None)
    return created.timestamp() if created is not None else time.time()


async def failed(session: AsyncSession, app: Application) -> None:
    try:
        fields = await _fields(session, app)
        trace.record(
            "apply.failed", "chain", _began(app), time.time(), ok=False,
            failure=failure_code(app.error), **fields,
        )  # fmt: skip
    except Exception as exc:  # a trace must never fail the application
        log.warning("apply trace: %s", type(exc).__name__)


async def sent(session: AsyncSession, app: Application) -> None:
    try:
        fields = await _fields(session, app)
        wrote, changed = edited(app)
        trace.record(
            "apply.sent", "chain", _began(app), time.time(),
            writer_answers=wrote, edited=changed, **fields,
        )  # fmt: skip
    except Exception as exc:
        log.warning("apply trace: %s", type(exc).__name__)
