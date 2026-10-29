"""form-check (Phase 8): the weekly live form test, Mondays at 07:00 in JAP_TZ, or now with
form-check/requested (Test forms now on the Status card, or make form-check). One step per
ATS reads one real form (pipeline/form_check.py); any failure sends form-check/failed for
the alert email. Nothing is ever submitted.
"""

from typing import Any

import inngest

from config import get_settings
from database.session import get_sessionmaker
from functions.client import inngest_client
from pipeline import form_check


async def targets_step() -> dict[str, str | None]:
    async with get_sessionmaker()() as session:
        return await form_check.targets(session)


async def check_step(ats: str, posting_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        row = await form_check.check(session, ats, posting_id)
    return {"id": str(row.id), "ok": row.ok, "fields": row.fields, "error": row.error}


@inngest_client.create_function(
    fn_id="form-check",
    trigger=[
        inngest.TriggerCron(cron=f"TZ={get_settings().jap_tz} 0 7 * * 1"),
        inngest.TriggerEvent(event="form-check/requested"),
    ],
    concurrency=[inngest.Concurrency(limit=1)],
    retries=1,
)
async def form_check_fn(ctx: inngest.Context) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for ats, posting_id in (await ctx.step.run("targets", targets_step)).items():
        if posting_id is None:
            results[ats] = {"skipped": "no open job on a board you watch"}
            continue
        results[ats] = await ctx.step.run(f"check-{ats}", check_step, ats, posting_id)
    failed = [r["id"] for r in results.values() if "id" in r and not r["ok"]]
    if failed:
        await ctx.step.send_event(
            "send-failed", inngest.Event(name="form-check/failed", data={"check_ids": failed})
        )
    return results
