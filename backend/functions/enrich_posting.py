"""enrich-posting (I3, I4): extract the empty fields, then embed. Both cached by hash.

Concurrency 4, 2 retries. A spend cap, a missing key or a blocked request stops this
posting without retrying; it is picked up again on the next run (its hash still differs).
"""

import uuid
from datetime import timedelta
from typing import Any

import inngest

from database.session import get_sessionmaker
from functions.client import inngest_client
from llm import PersonalDataBlocked, SpendCapReached
from llm.client import MissingApiKey
from pipeline.enrich import EmbedFailed, embed_posting, extract_posting


async def extract_step(posting_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        try:
            result = await extract_posting(session, uuid.UUID(posting_id))
        except (SpendCapReached, MissingApiKey, PersonalDataBlocked) as exc:
            raise inngest.NonRetriableError(str(exc)) from exc
    return {
        "skipped": result.skipped,
        "fields": result.fields,
        # Shown in the Inngest UI: what went to the model, after redaction.
        "sent_to_model": result.sent_to_model,
    }


async def embed_step(posting_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        try:
            done = await embed_posting(session, uuid.UUID(posting_id))
        except EmbedFailed as exc:
            raise inngest.RetryAfterError(str(exc), timedelta(seconds=10)) from exc
    return {"embedded": done}


@inngest_client.create_function(
    fn_id="enrich-posting",
    trigger=inngest.TriggerEvent(event="posting/enrich.requested"),
    concurrency=[inngest.Concurrency(limit=4)],
    retries=2,
)
async def enrich_posting(ctx: inngest.Context) -> dict[str, Any]:
    posting_id = str(ctx.event.data["posting_id"])
    extracted = await ctx.step.run("extract", extract_step, posting_id)
    try:
        embedded = await ctx.step.run("embed", embed_step, posting_id)
    except inngest.StepError as exc:  # Ollama down: extraction still counts
        embedded = {"embedded": False, "error": exc.message}
    return {"posting_id": posting_id, **extracted, **embedded}
