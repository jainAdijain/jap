"""Creating runs (T4): a run row exists before its event is sent, and repeats collapse.

The one_queued_run index allows a single queued run. A trigger that finds one reuses it
instead of adding another; run-pipeline's concurrency of 1 makes it wait for the active run.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Posting, Run, WatchedBoard


async def queue_run(
    session: AsyncSession, trigger: str, mode: str, scope: dict[str, Any] | None = None
) -> tuple[Run, bool]:
    """The queued run to use, and whether it is new (only a new one needs its event sent)."""
    queued = await session.scalar(select(Run).where(Run.status == "queued"))
    if queued is not None:
        return queued, False
    run = Run(trigger=trigger, mode=mode, scope=scope or {})
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:  # another trigger queued one a moment ago
        await session.rollback()
        queued = await session.scalar(select(Run).where(Run.status == "queued"))
        assert queued is not None
        return queued, False
    await session.refresh(run)
    return run, True


async def start_run(session: AsyncSession, run_id: uuid.UUID) -> list[dict[str, str]]:
    """Mark the run running and list the boards it fetches (only active ones, I8)."""
    run = await session.get(Run, run_id)
    if run is None:
        raise LookupError(f"run {run_id} not found")
    if run.status == "queued":
        run.status = "running"
        run.started_at = datetime.now(UTC)
    query = (
        select(WatchedBoard)
        .where(WatchedBoard.active, WatchedBoard.origin != "demo")  # demo boards hold fixtures
        .order_by(WatchedBoard.display_name)
    )
    board_ids = (run.scope or {}).get("board_ids")
    if board_ids:
        query = query.where(WatchedBoard.id.in_([uuid.UUID(b) for b in board_ids]))
    boards = list(await session.scalars(query))
    await session.commit()
    return [{"board_id": str(b.id), "ats": b.ats} for b in boards]


async def finish_run(
    session: AsyncSession, run_id: uuid.UUID, counts: dict[str, Any], error: str | None = None
) -> None:
    run = await session.get(Run, run_id)
    if run is None:
        return
    run.status = "failed" if error else "succeeded"
    run.ended_at = datetime.now(UTC)
    run.counts = {**(run.counts or {}), **counts}
    run.error = error
    await session.commit()


async def embeddings_pending(session: AsyncSession) -> int:
    """Open postings still waiting for their embedding (near-match dedup needs it)."""
    count = await session.scalar(
        select(func.count())
        .select_from(Posting)
        .where(
            Posting.state == "open",
            Posting.embedded_hash.is_distinct_from(Posting.description_hash),
        )
    )
    return int(count or 0)
