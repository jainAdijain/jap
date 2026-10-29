"""enrich-posting (I3, I4): embed, then extract the empty fields. Both cached by hash.

The embedding comes first because it is local and quick, and dedup waits for it. Once the
fields are extracted, the posting's listing is refreshed (skills, work mode, seniority).
Concurrency 4, 2 retries. A spend cap, a missing key or a blocked request stops this
posting without retrying; it is picked up again on the next run (its hash still differs).
"""

import uuid
from datetime import timedelta
from typing import Any

import inngest

from database.models import Posting
from database.session import get_sessionmaker
from functions.client import inngest_client
from llm import PersonalDataBlocked, SpendCapReached
from llm.client import MissingApiKey
from pipeline.dedup import refresh_listings
from pipeline.enrich import EmbedFailed, embed_posting, extract_posting
from pipeline.matching import refilter_from_profile


async def extract_step(posting_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        try:
            result = await extract_posting(session, uuid.UUID(posting_id))
        except (SpendCapReached, MissingApiKey, PersonalDataBlocked) as exc:
            raise inngest.NonRetriableError(str(exc)) from exc
        posting = await session.get(Posting, uuid.UUID(posting_id))
        if posting is not None and posting.listing_id is not None and not result.skipped:
            await refresh_listings(session, {posting.listing_id})
            await refilter_from_profile(session, [posting.listing_id])  # new skills, work mode
            await session.commit()
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
    try:
        embedded = await ctx.step.run("embed", embed_step, posting_id)
    except inngest.StepError as exc:  # Ollama down: extraction still goes ahead
        embedded = {"embedded": False, "error": exc.message}
    extracted = await ctx.step.run("extract", extract_step, posting_id)
    return {"posting_id": posting_id, **extracted, **embedded}
