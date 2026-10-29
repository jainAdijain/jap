"""notify-applied (E1 to E3): one "Applied: Role at Company" email per Applied application.

send and mark-sent are separate steps, so a retry after a sent email never sends it again,
and an application whose email_sent_at is set is skipped. A final failure is logged and
never changes the application's status (E2).
"""

import logging
import uuid

import inngest

from database.session import get_sessionmaker
from functions.client import inngest_client
from pipeline import notify

log = logging.getLogger(__name__)


async def send_step(app_id: str) -> bool:
    async with get_sessionmaker()() as session:
        mail = await notify.build(session, uuid.UUID(app_id))
    if mail is None:
        return False  # already sent
    await notify.send(mail)
    return True


async def mark_step(app_id: str) -> None:
    async with get_sessionmaker()() as session:
        await notify.mark_sent(session, uuid.UUID(app_id))


async def _failed(ctx: inngest.Context) -> None:
    error = ctx.event.data.get("error")
    message = error.get("message") if isinstance(error, dict) else error
    log.error("Confirmation email not sent: %s", message)


@inngest_client.create_function(
    fn_id="notify-applied",
    trigger=inngest.TriggerEvent(event="application/submitted"),
    retries=4,
    on_failure=_failed,
)
async def notify_applied(ctx: inngest.Context) -> bool:
    app_id = str(ctx.event.data["application_id"])
    if await ctx.step.run("send", send_step, app_id):
        await ctx.step.run("mark-sent", mark_step, app_id)
        return True
    return False
