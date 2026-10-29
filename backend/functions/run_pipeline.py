"""run-pipeline (T4, T5, I7, D6, F1): one run. fetch, dedup, then filter (the match rule
on every stored listing); the apply steps join it in a later phase. Runs are started by the
Run dialog, the Daily run, a profile save and an accepted resume (functions/triggers.py).

Concurrency 1 across the app, so a queued run waits for the active one. Each board is
fetched by its own fetch-board run, invoked in parallel. Dedup waits (a few minutes at
most) for new postings' embeddings, which Ollama makes in seconds, so near matches count.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import inngest

from config import get_settings
from database.models import Run
from database.session import get_sessionmaker
from functions.client import inngest_client
from functions.fetch_board import fetch_board
from llm.ollama import ping_ollama
from pipeline.dedup import dedup
from pipeline.matching import refilter_from_profile
from pipeline.runs import embeddings_pending, finish_run, start_run
from schemas.job import ATS
from sources import ADAPTERS

WAIT_ROUNDS, WAIT_SECONDS = 24, 5  # up to two minutes for embeddings
TURN_ROUNDS, TURN_SECONDS = 360, 10  # up to an hour waiting for the run before this one
COUNTED = ("fetched", "new", "changed", "unchanged", "reopened", "closed", "enrich_queued")


async def start_step(run_id: str) -> list[dict[str, Any]] | None:
    async with get_sessionmaker()() as session:
        return await start_run(session, uuid.UUID(run_id))


async def pending_step() -> int:
    """Postings waiting for an embedding, or 0 when Ollama is down (no point waiting)."""
    settings = get_settings()
    if not (await ping_ollama(settings.ollama_base_url, settings.jap_model_embed)).ok:
        return 0
    async with get_sessionmaker()() as session:
        return await embeddings_pending(session)


async def dedup_step(run_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        run = await session.get(Run, uuid.UUID(run_id))
        since = run.started_at if run is not None else datetime.now(UTC)
        counts = await dedup(session, since)
    return counts.as_dict()


async def filter_step() -> dict[str, int]:
    async with get_sessionmaker()() as session:
        listed = await refilter_from_profile(session)
        await session.commit()
    return {"listed": listed}


async def finish_step(run_id: str, counts: dict[str, Any], error: str | None) -> None:
    async with get_sessionmaker()() as session:
        await finish_run(session, uuid.UUID(run_id), counts, error)


async def _failed(ctx: inngest.Context) -> None:
    original = ctx.event.data.get("event")
    data = original.get("data") if isinstance(original, dict) else None
    run_id = data.get("run_id") if isinstance(data, dict) else None
    error = ctx.event.data.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    if isinstance(run_id, str):
        await finish_step(run_id, {}, message or "Run failed after retries")


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, Any] = {k: 0 for k in COUNTED}
    counts["boards"] = len(results)
    counts["boards_failed"] = 0
    errors: list[str] = []
    for r in results:
        if not r.get("ok"):
            counts["boards_failed"] += 1
            errors.append(str(r.get("error") or "fetch failed"))
            continue
        for k in COUNTED:
            counts[k] += int(r.get(k) or 0)
    if errors:
        counts["board_errors"] = errors[:20]
    return counts


@inngest_client.create_function(
    fn_id="run-pipeline",
    trigger=inngest.TriggerEvent(event="run/requested"),
    concurrency=[inngest.Concurrency(limit=1)],
    retries=2,
    on_failure=_failed,
)
async def run_pipeline(ctx: inngest.Context) -> dict[str, Any]:
    run_id = str(ctx.event.data["run_id"])
    boards = await ctx.step.run("start", start_step, run_id)
    for i in range(TURN_ROUNDS):  # one run at a time (T4); see pipeline/runs.py
        if boards is not None:
            break
        await ctx.step.sleep(f"wait-turn-{i}", timedelta(seconds=TURN_SECONDS))
        boards = await ctx.step.run(f"start-{i}", start_step, run_id)
    if boards is None:
        raise inngest.NonRetriableError("The run before this one did not finish within an hour")

    def fetch(board: dict[str, Any]) -> Any:
        async def go() -> dict[str, Any]:
            data = {"run_id": run_id, "board_id": board["board_id"], "roles": board.get("roles"),
                    "host": ADAPTERS[ATS(board["ats"])].host}  # fmt: skip
            try:
                return await ctx.step.invoke(
                    f"fetch-{board['board_id']}", function=fetch_board, data=data
                )
            except inngest.StepError as exc:
                return {"board_id": board["board_id"], "ok": False, "error": exc.message}

        return go

    results = list(await ctx.group.parallel(tuple(fetch(b) for b in boards))) if boards else []
    counts = summarize(results)
    for i in range(WAIT_ROUNDS):
        if not await ctx.step.run(f"embeddings-pending-{i}", pending_step):
            break
        await ctx.step.sleep(f"wait-embeddings-{i}", timedelta(seconds=WAIT_SECONDS))
    counts.update(await ctx.step.run("dedup", dedup_step, run_id))
    counts.update(await ctx.step.run("filter", filter_step))
    await ctx.step.run("finish", finish_step, run_id, counts, None)
    return counts
