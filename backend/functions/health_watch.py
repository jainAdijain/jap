"""health-watch (PRD O3, O6): every hour, delete old log rows and email health alerts.

Steps: cleanup (log events and route timings past 30 days), find (the alerts due now,
observability/alerts.py), then one send step per alert, which sends and remembers the key
so a retry or the next hour doesn't send it again. JAP_ALERTS_ENABLED=false skips sending.
"""

import logging
from typing import Any

import inngest

from config import get_settings
from database.session import get_sessionmaker
from functions.client import inngest_client
from observability import alerts
from observability.logs import event
from pipeline import notify

log = logging.getLogger(__name__)


async def cleanup_step() -> dict[str, int]:
    async with get_sessionmaker()() as session:
        return await alerts.cleanup(session)


async def find_step() -> list[dict[str, Any]]:
    async with get_sessionmaker()() as session:
        return [a.as_dict() for a in await alerts.due(session)]


async def send_step(data: dict[str, Any]) -> str:
    alert = alerts.Alert(**data)
    async with get_sessionmaker()() as session:
        if await alerts.already_sent(session, alert.key):
            return "already sent"
        mail = await alerts.mail_for(session, alert)
        await notify.send(mail)
        await alerts.mark_sent(session, alert)
    event(log, "alert.sent", f"Health alert sent: {alert.subject}", key=alert.key)
    return "sent"


@inngest_client.create_function(
    fn_id="health-watch",
    trigger=[
        inngest.TriggerCron(cron=f"TZ={get_settings().jap_tz} 40 * * * *"),
        inngest.TriggerEvent(event="health/check-requested"),
    ],
    concurrency=[inngest.Concurrency(limit=1)],
    retries=2,
)
async def health_watch(ctx: inngest.Context) -> dict[str, Any]:
    cleaned = await ctx.step.run("cleanup", cleanup_step)
    due = await ctx.step.run("find", find_step)
    if not get_settings().jap_alerts_enabled:
        return {"cleaned": cleaned, "due": [a["key"] for a in due], "sent": "alerts are off"}
    results = {}
    for alert in due:
        results[alert["key"]] = await ctx.step.run(f"send-{alert['key']}", send_step, alert)
    return {"cleaned": cleaned, "sent": results}
