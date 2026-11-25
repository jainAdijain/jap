"""An email when an AI run breaks or does nothing (PRD 1.35, O25).

The runner (agent/run.sh) works on the Mac and cannot send email, so it is told nothing of
a run that went wrong but its run log on the Mac: the five mornings of 21 to 25 Nov 2026
saved nothing between them but one, and nobody was told. After every run the runner hands
its trace file to JAP (pipeline/agent_run_trace.py, O22), and this looks at it there. A run
that failed, stopped at the harness's usage limit, or, for the reader, saved nothing while
postings waited sends one email to the address on My profile.

One a day for each job, with the alert keys and the mailer of the health alerts (O6), so the
hourly tries of a broken morning (AH6) send one email, not fifteen. A trace that waited and
is handed over later is judged again and the day's key stops a second email; one of a run
more than 36 hours old sends none. JAP_ALERTS_ENABLED=false sends none.

The email says what the trace file says: counts and causes, never text. Nothing here may
change what the trace door prints or returns: maybe() swallows its own errors.
"""

import asyncio
import logging
import re
import sys
import time
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import get_settings
from database.session import get_sessionmaker
from observability import alerts
from observability.logs import event
from pipeline import notify

log = logging.getLogger(__name__)

MAX_AGE_S = 36 * 3600
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,79}$")
STAMP = re.compile(r"^\d{8}-\d{6}-\d{1,8}$")
# What to do about each cause of a failed session or run (O18), in the email.
WHAT_TO_DO = {
    "slept": "The Mac slept during the run. Keep it awake and on power around the run's "
    "time; the schedule tries again every hour until 22:00.",
    "unreachable": "The Mac could not reach the stack (the ssh to the VM, or Docker, "
    "failed). Check the Mac's network and that ssh to the VM works; the schedule tries "
    "again every hour until 22:00.",
    "no_answer": "JAP did not answer at all. Check that the stack is up (make ps).",
    "not_logged_in": "The harness is signed out on the Mac. Sign in again (Claude Code: run "
    "claude, then /login).",
    "usage_limit": "The harness's plan limit is reached. What is left waits for the next run "
    "after the limit resets.",
    "timeout": "A session ran past its time limit (JAP_AGENT_TIMEOUT_MIN).",
    "no_output": "A session ended without writing its file. Its harness.log is in the batch "
    "folder, kept 3 days.",
    "export_failed": "JAP answered an export with an error. See make errors.",
    "import_failed": "JAP answered an import with an error. See make errors.",
}


def _count(value: Any) -> int:
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return value if ok else 0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _name(value: Any, default: str) -> str:
    return value if isinstance(value, str) and NAME.match(value) else default


def judge(run: Any, now: float | None = None) -> alerts.Alert | None:
    """The alert this run's trace file calls for, or None (O25)."""
    run = _dict(run)
    job, ended, code = run.get("job"), run.get("ended"), run.get("exit")
    if job not in ("reader", "writer") or isinstance(ended, bool):
        return None
    if not isinstance(ended, (int, float)) or (now or time.time()) - ended > MAX_AGE_S:
        return None
    totals, before = _dict(run.get("totals")), _dict(run.get("before"))
    after = _dict(run.get("after"))
    saved = _count(totals.get("saved"))
    waiting = _count(before.get("unread")) + _count(before.get("pairs"))
    if code == 1:
        what = "failed"
    elif code == 75:
        what = "stopped at the harness's usage limit"
    elif code == 0 and job == "reader" and waiting and not saved and _count(totals.get("sessions")):
        what = "saved nothing"
    else:
        return None
    settings = get_settings()
    zone = ZoneInfo(settings.jap_tz)
    started = run.get("started")
    began = datetime.fromtimestamp(
        started if isinstance(started, (int, float)) and not isinstance(started, bool) else ended,
        UTC,
    ).astimezone(zone)
    causes = {k: _count(n) for k, n in _dict(totals.get("causes")).items() if k in WHAT_TO_DO}
    cause = run.get("cause")
    if cause in WHAT_TO_DO and not causes:
        causes[cause] = 1
    stamp = run.get("stamp") if isinstance(run.get("stamp"), str) else ""
    harness = _name(run.get("harness"), "the harness")
    model = _name(run.get("model"), "default")
    minutes = max(int(ended - began.timestamp()) // 60, 0)
    lines = [
        f"The AI {job} run of {began:%d %b %Y at %H:%M} ({settings.jap_tz}) on {harness} "
        f"({model}) {what} after {minutes} min.",
        "",
        f"Sessions: {_count(totals.get('sessions'))}, of them failed: "
        f"{_count(totals.get('failed'))}"
        + (
            " (" + ", ".join(f"{k} {n}" for k, n in sorted(causes.items())) + ")."
            if causes
            else "."
        ),
    ]
    if job == "reader":
        lines.append(
            f"Saved {saved} postings. Unread before: {_count(before.get('unread'))}, "
            f"after: {_count(after.get('unread', before.get('unread')))}."
        )
    else:
        lines.append(
            f"Jobs at Needs you before: {_count(before.get('jobs'))}, after: "
            f"{_count(after.get('jobs', before.get('jobs')))}. Left out: "
            f"{_count(totals.get('left_out'))}."
        )
    if causes:
        lines += ["", "What to do:"] + [f"- {WHAT_TO_DO[k]}" for k in sorted(causes)]
    if STAMP.match(stamp):
        lines += ["", f"The run log on the Mac: agent/runs/{stamp}-{job}.log in the data folder."]
    return alerts.Alert(
        f"agent-run:{job}:{began:%Y-%m-%d}", f"JAP alert: the AI {job} run {what}", lines
    )


async def send(run: Any) -> str:
    """Email the alert this run calls for, once a day per job: what happened, in a word."""
    alert = judge(run)
    if alert is None:
        return "none"
    if not get_settings().jap_alerts_enabled:
        return "alerts are off"
    async with get_sessionmaker()() as session:
        if await alerts.already_sent(session, alert.key):
            return "already sent"
        await notify.send(await alerts.mail_for(session, alert))
        await alerts.mark_sent(session, alert)
    event(log, "alert.sent", f"Health alert sent: {alert.subject}", key=alert.key)
    return "sent"


def maybe(run: Any) -> None:
    """send(), from the trace door: never a word on standard output (the runner reads its
    last line) and never an error (a run's trace still goes out without its email)."""
    try:
        if judge(run) is not None:
            asyncio.run(send(run))
    except Exception as exc:
        print(f"jap alert: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
