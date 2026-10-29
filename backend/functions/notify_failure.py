"""notify-failure (Phase 8): the alert email when a run fails or loses a board, when an
application ends Failed, or when the weekly form test can't read a form (pipeline/alerts.py).

Like notify-applied, send and mark-sent are separate steps, so a retry after a sent alert
never sends it twice. A final failure is only logged: an alert never changes a status.
"""

import logging
import uuid
from typing import Any

import inngest

from database.session import get_sessionmaker
from functions.client import inngest_client
from pipeline import alerts, notify

log = logging.getLogger(__name__)


async def send_step(data: dict[str, Any]) -> str | None:
    """Send the alert the event names: "run" or "application" when one was sent (and
    needs marking), "form_check" for form tests (nothing to mark), None when not needed."""
    async with get_sessionmaker()() as session:
        if data.get("run_id"):
            kind, mail = "run", await alerts.run_alert(session, uuid.UUID(str(data["run_id"])))
        elif data.get("application_id"):
            app_id = uuid.UUID(str(data["application_id"]))
            kind, mail = "application", await alerts.application_alert(session, app_id)
        else:
            ids = [uuid.UUID(str(i)) for i in data.get("check_ids") or []]
            kind, mail = "form_check", await alerts.form_check_alert(session, ids)
    if mail is None:
        return None
    await notify.send(mail)
    return kind


async def mark_step(kind: str, target: str) -> None:
    async with get_sessionmaker()() as session:
        await alerts.mark_sent(session, kind, uuid.UUID(target))


async def _failed(ctx: inngest.Context) -> None:
    error = ctx.event.data.get("error")
    message = error.get("message") if isinstance(error, dict) else error
    log.error("Failure alert not sent: %s", message)


@inngest_client.create_function(
    fn_id="notify-failure",
    trigger=[
        inngest.TriggerEvent(event="run/failed"),
        inngest.TriggerEvent(event="application/failed"),
        inngest.TriggerEvent(event="form-check/failed"),
    ],
    retries=4,
    on_failure=_failed,
)
async def notify_failure(ctx: inngest.Context) -> str | None:
    data = dict(ctx.event.data)
    kind = await ctx.step.run("send", send_step, data)
    if kind in ("run", "application"):
        target = str(data["run_id"] if kind == "run" else data["application_id"])
        await ctx.step.run("mark-sent", mark_step, kind, target)
    return kind
