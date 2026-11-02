"""An application's timeline (PRD O8): what JAP did for it, step by step.

Built from the log events that name the application (apply steps, Inngest steps, warnings
and errors) and its model calls (llm_call: purpose, model, time taken, cost, never the
text). Log events are kept 30 days; model calls stay as long as the application.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Application, LlmCall, LogEvent
from observability.alerts import KEEP_LOGS
from schemas.status import Timeline, TimelineItem

PURPOSES = {
    "answer": "Wrote an answer",
    "check": "Checked an answer",
    "claim_check": "Checked the cover letter's claims",
    "cover_letter": "Wrote the cover letter",
}


async def timeline(session: AsyncSession, app: Application) -> Timeline:
    items: list[TimelineItem] = [
        TimelineItem(
            at=app.created_at, kind="status", event="created", text="Started", level="info"
        )
    ]
    logs = await session.scalars(
        select(LogEvent).where(LogEvent.application_id == app.id).order_by(LogEvent.created_at)
    )
    for row in logs:
        fields = dict(row.fields or {})
        fields.pop("exc", None)
        items.append(
            TimelineItem(
                at=row.created_at,
                kind="step",
                event=row.event,
                text=row.message,
                level=row.level,
                duration_ms=fields.get("duration_ms"),
                fields=fields,
            )
        )
    calls = await session.scalars(
        select(LlmCall).where(LlmCall.application_id == app.id).order_by(LlmCall.created_at)
    )
    for c in calls:
        what = PURPOSES.get(c.purpose, f"Model call: {c.purpose}")
        text = f"{what} with {c.model}"
        if c.error:
            text += f": {c.error}"
        items.append(
            TimelineItem(
                at=c.created_at,
                kind="model",
                event=f"llm.{c.purpose}",
                text=text,
                level="warning" if c.error else "info",
                duration_ms=c.latency_ms,
                cost_usd=float(c.cost_usd) if c.cost_usd is not None else None,
                fields={"tokens_in": c.tokens_in, "tokens_out": c.tokens_out},
            )
        )
    if app.submitted_at:
        items.append(
            TimelineItem(
                at=app.submitted_at, kind="status", event="applied", text="Applied", level="info"
            )
        )
    if app.email_sent_at:
        items.append(
            TimelineItem(
                at=app.email_sent_at,
                kind="status",
                event="emailed",
                text="Confirmation email sent",
                level="info",
            )
        )
    items.sort(key=lambda i: i.at)
    return Timeline(application_id=app.id, status=app.status, items=items, kept_days=KEEP_LOGS.days)


async def for_id(session: AsyncSession, app_id: uuid.UUID) -> Timeline | None:
    app = await session.get(Application, app_id)
    return await timeline(session, app) if app else None
