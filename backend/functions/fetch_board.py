"""fetch-board (I1, I2, I5 to I7): one board's jobs into posting, then extraction queued.

Throttled to 1 start per second per ATS host, 2 retries. A board that 404s is marked
last_ok = false and the run carries on; one that keeps failing after its retries is
reported in the run's counts instead of failing the whole run.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import inngest

from database.models import WatchedBoard
from database.session import get_sessionmaker
from functions.client import inngest_client
from pipeline.ingest import close_missing, store_jobs
from schemas.job import ATS
from sources import ADAPTERS, BoardNotFound, SourceChanged, SourceUnavailable, base

EVENTS_PER_SEND = 100


async def _mark(board_id: uuid.UUID, ok: bool) -> None:
    async with get_sessionmaker()() as session:
        board = await session.get(WatchedBoard, board_id)
        if board is not None:
            board.last_checked_at = datetime.now(UTC)
            board.last_ok = ok
            await session.commit()


async def fetch_step(board_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        board = await session.get(WatchedBoard, uuid.UUID(board_id))
        if board is None:
            raise inngest.NonRetriableError("board not found")
        adapter = ADAPTERS[ATS(board.ats)]
        token, name = board.board_token, board.display_name
    try:
        async with base.client() as http:
            jobs = await adapter.fetch(http, token, name)
    except BoardNotFound:
        await _mark(uuid.UUID(board_id), ok=False)
        return {"ok": False, "error": f"{name}: board not found on {board.ats}"}
    except SourceChanged as exc:
        await _mark(uuid.UUID(board_id), ok=False)
        raise inngest.NonRetriableError(str(exc)) from exc
    except SourceUnavailable:
        raise  # retried by Inngest
    async with get_sessionmaker()() as session:
        stored = await store_jobs(session, uuid.UUID(board_id), jobs)
    await _mark(uuid.UUID(board_id), ok=True)
    return {
        "ok": True,
        "fetched": len(jobs),
        "new": stored.new,
        "changed": stored.changed,
        "unchanged": stored.unchanged,
        "reopened": stored.reopened,
        "seen": [j.external_id for j in jobs],
        "to_enrich": stored.to_enrich,
    }


async def close_step(board_id: str, seen: list[str]) -> int:
    async with get_sessionmaker()() as session:
        return await close_missing(session, uuid.UUID(board_id), seen)


@inngest_client.create_function(
    fn_id="fetch-board",
    trigger=inngest.TriggerEvent(event="board/fetch.requested"),
    throttle=inngest.Throttle(key="event.data.host", limit=1, period=timedelta(seconds=1)),
    retries=2,
)
async def fetch_board(ctx: inngest.Context) -> dict[str, Any]:
    board_id = str(ctx.event.data["board_id"])
    try:
        fetched = await ctx.step.run("fetch", fetch_step, board_id)
    except inngest.StepError as exc:
        return {"board_id": board_id, "ok": False, "error": exc.message or "fetch failed"}
    if not fetched["ok"]:
        return {"board_id": board_id, **fetched}
    closed = await ctx.step.run("close-missing", close_step, board_id, fetched["seen"])
    to_enrich: list[str] = fetched["to_enrich"]
    for i in range(0, len(to_enrich), EVENTS_PER_SEND):
        await ctx.step.send_event(
            f"enrich-{i // EVENTS_PER_SEND}",
            [
                inngest.Event(name="posting/enrich.requested", data={"posting_id": pid})
                for pid in to_enrich[i : i + EVENTS_PER_SEND]
            ],
        )
    return {
        "board_id": board_id,
        "ok": True,
        **{k: fetched[k] for k in ("fetched", "new", "changed", "unchanged", "reopened")},
        "closed": closed,
        "enrich_queued": len(to_enrich),
    }
