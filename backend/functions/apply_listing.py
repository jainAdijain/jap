"""apply-listing (Apply flow, A1 to A4): one listing, from check to Applied.

Steps: create-attempt (check, then insert the application), snapshot (freeze the JD),
read-form (Playwright), map-fields (profile, resume, answer bank, drafted answers), then
cover-letter when the form's cover letter field is mandatory (C1), then Needs input or
Ready for review waits, then submit (cap and gap first) and
application/submitted for the email. Review mode always stops at Ready for review until I
press Approve; nothing is submitted before. Automatic mode submits unless something about
the job is unclear (A3). A Failed attempt sends application/failed for the alert email.

Concurrency 1: Inngest counts executing steps, not runs, so this means one browser at a
time while attempts waiting for me (Ready for review, Needs input) hold no slot. A press
lands on the application row first (the API writes it, then sends the event), and each
wait is followed by a read of the row, so an Approve sent a moment before the wait began
is picked up when the short first wait ends.
"""

from datetime import datetime, timedelta
from typing import Any

import inngest
from sqlalchemy import select

from database.models import Application
from database.session import get_sessionmaker
from functions.client import inngest_client
from pipeline.apply import flow

FIRST_WAIT = timedelta(minutes=2)
NEXT_WAIT = timedelta(days=1)
WAIT_ROUNDS = 15  # about 14 days in all
MAX_PASSES = 5  # Needs input answered, mapped again, at most this often


async def create_step(listing_id: str, run_id: str | None) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        try:
            return {"application_id": await flow.create_attempt(session, listing_id, run_id)}
        except flow.Stop as exc:
            return {"stopped": str(exc)}


async def snapshot_step(app_id: str) -> str:
    async with get_sessionmaker()() as session:
        return await flow.snapshot(session, app_id)


async def read_step(app_id: str, url: str) -> int:
    """A FormError (site change, timeout) is retried by Inngest, then fails the attempt."""
    async with get_sessionmaker()() as session:
        return await flow.read_and_save(session, app_id, url)


async def map_step(app_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        try:
            m = await flow.map_fields(session, app_id)
        except flow.Stop as exc:
            return {"status": "stopped", "reason": str(exc)}
        return {"status": m.status, "drafted": m.drafted, "pending": m.pending}


async def letter_step(app_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        try:
            return await flow.write_cover_letter(session, app_id)
        except flow.Stop as exc:
            return {"status": "stopped", "reason": str(exc)}


async def status_step(app_id: str) -> str:
    async with get_sessionmaker()() as session:
        return await flow.status_of(session, app_id)


async def submit_step(app_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        try:
            return await flow.submit(session, app_id)
        except flow.Stop as exc:
            return {"status": "stopped", "reason": str(exc)}


async def expire_step(app_id: str, what: str) -> None:
    async with get_sessionmaker()() as session:
        await flow.expire(session, app_id, what)


async def _failed(ctx: inngest.Context) -> None:
    """After retries: the listing's attempt becomes Failed with the reason (U15)."""
    original = ctx.event.data.get("event")
    data = original.get("data") if isinstance(original, dict) else None
    listing_id = data.get("listing_id") if isinstance(data, dict) else None
    error = ctx.event.data.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(listing_id, str):
        return
    async with get_sessionmaker()() as session:
        app_id = await session.scalar(
            select(Application.id).where(
                Application.listing_id == listing_id, Application.status.in_(flow.ACTIVE)
            )
        )
        if app_id is not None:
            await flow.fail_by_id(session, str(app_id), message or "Apply failed after retries")
    if app_id is not None:
        await inngest_client.send(
            inngest.Event(name="application/failed", data={"application_id": str(app_id)})
        )


async def _alert(ctx: inngest.Context, app_id: str) -> None:
    """The failure alert email (notify-failure)."""
    await ctx.step.send_event(
        "send-failed", inngest.Event(name="application/failed", data={"application_id": app_id})
    )


async def wait_for_me(ctx: inngest.Context, app_id: str, event: str, waiting: str, tag: str) -> str:
    """Wait while the application is in `waiting` (Ready for review or Needs input) and
    return the status it moved to, or "expired" after about 14 days."""
    for i in range(WAIT_ROUNDS):
        status = await ctx.step.run(f"{tag}-status-{i}", status_step, app_id)
        if status != waiting:
            return str(status)
        await ctx.step.wait_for_event(
            f"{tag}-wait-{i}",
            event=event,
            if_exp=f"async.data.application_id == '{app_id}'",
            timeout=FIRST_WAIT if i == 0 else NEXT_WAIT,
        )
    status = await ctx.step.run(f"{tag}-status-last", status_step, app_id)
    return str(status) if status != waiting else "expired"


@inngest_client.create_function(
    fn_id="apply-listing",
    trigger=inngest.TriggerEvent(event="listing/apply.requested"),
    concurrency=[inngest.Concurrency(limit=1)],
    retries=2,
    cancel=[
        inngest.Cancel(
            event="application/cancelled",
            if_exp="event.data.listing_id == async.data.listing_id",
        )
    ],
    on_failure=_failed,
)
async def apply_listing(ctx: inngest.Context) -> dict[str, Any]:
    listing_id = str(ctx.event.data["listing_id"])
    run_id = ctx.event.data.get("run_id")
    created = await ctx.step.run(
        "create-attempt", create_step, listing_id, str(run_id) if run_id else None
    )
    if "stopped" in created:
        return created
    app_id = str(created["application_id"])
    url = await ctx.step.run("snapshot", snapshot_step, app_id)
    await ctx.step.run("read-form", read_step, app_id, url)

    status = "preparing"
    for p in range(MAX_PASSES):
        mapped = await ctx.step.run(f"map-fields-{p}", map_step, app_id)
        status = str(mapped["status"])
        if status == "cover_letter":
            written = await ctx.step.run(f"cover-letter-{p}", letter_step, app_id)
            status = str(written["status"])
        if status == "needs_input":
            status = await wait_for_me(
                ctx, app_id, "application/answered", "needs_input", f"input-{p}"
            )
            if status == "preparing":
                continue  # answered: map again with the answers (back to step 5)
        if status == "ready_for_review":
            status = await wait_for_me(
                ctx, app_id, "application/approved", "ready_for_review", f"review-{p}"
            )
        break
    if status == "expired":
        await ctx.step.run("expire", expire_step, app_id, "answer or approval")
        await _alert(ctx, app_id)
        return {"application_id": app_id, "status": "failed"}
    if status != "applying":
        return {"application_id": app_id, "status": status}

    for i in range(40):
        result = await ctx.step.run(f"submit-{i}", submit_step, app_id)
        if "wait_until" in result:
            await ctx.step.sleep_until(
                f"submit-wait-{i}", datetime.fromisoformat(str(result["wait_until"]))
            )
            continue
        if result.get("status") == "applied":
            await ctx.step.send_event(
                "send-submitted",
                inngest.Event(name="application/submitted", data={"application_id": app_id}),
            )
        elif result.get("status") == "failed":
            await _alert(ctx, app_id)
        return {"application_id": app_id, **result}
    raise inngest.NonRetriableError("Waited too long for the daily cap")
