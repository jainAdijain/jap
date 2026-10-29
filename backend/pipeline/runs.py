"""Creating runs (T4): a run row exists before its event is sent, and repeats collapse.

The one_queued_run index allows a single queued run. A trigger that finds one reuses it
instead of adding another, widening its scope to cover both asks; run-pipeline's
concurrency of 1 makes it wait for the active run.

Inngest's concurrency limit counts executing steps, not runs: a run waiting on its board
fetches does not hold the slot, so a queued run's first step could start beside it. So a
run claims its turn itself (start_run returns None while another run is going) and keeps
waiting as queued, which also keeps it the run that later triggers join.

A scope (the Run dialog, T3) narrows a run to some boards and some roles. Roles are matched
the way the match rule matches them (whole words, Sr to Senior, engineer = developer), on
the posting's title, so only that role's jobs are stored, updated or closed.
"""

import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Posting, Run, WatchedBoard
from pipeline.canonical import canonical_role
from pipeline.matching import role_pattern

SCOPE_KEYS = ("board_ids", "roles")
# A run still marked running after this long is taken as crashed and no longer blocks.
STALE_RUN = timedelta(hours=2)


def merge_scope(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """The scope covering both: a key missing on either side means everything."""
    out: dict[str, Any] = {}
    for key in SCOPE_KEYS:
        if a.get(key) and b.get(key):
            out[key] = list(dict.fromkeys([*a[key], *b[key]]))
    return out


def role_matcher(roles: Iterable[str] | None) -> re.Pattern[str] | None:
    """A regex for a job title in one of these roles, or None when every role counts."""
    patterns = [p for r in roles or () if (p := role_pattern(r))]
    return re.compile("|".join(f"(?:{p})" for p in patterns)) if patterns else None


def in_scope(title: str, matcher: re.Pattern[str] | None) -> bool:
    return matcher is None or bool(matcher.search(canonical_role(title) or title.lower()))


async def queue_run(
    session: AsyncSession, trigger: str, mode: str, scope: dict[str, Any] | None = None
) -> tuple[Run, bool]:
    """The queued run to use, and whether it is new (only a new one needs its event sent)."""
    queued = await session.scalar(select(Run).where(Run.status == "queued"))
    if queued is not None:
        merged = merge_scope(queued.scope or {}, scope or {})
        if merged != (queued.scope or {}):
            queued.scope = merged
            await session.commit()
            await session.refresh(queued)
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


async def start_run(session: AsyncSession, run_id: uuid.UUID) -> list[dict[str, Any]] | None:
    """Mark the run running and list the boards it fetches (only active ones, I8), each
    with the roles it is scoped to (None for every role). None while another run is going:
    this one stays queued and tries again (T4)."""
    run = await session.get(Run, run_id, with_for_update=True)
    if run is None:
        raise LookupError(f"run {run_id} not found")
    if run.status == "queued":
        other = await session.scalar(
            select(Run.id).where(
                Run.status == "running",
                Run.id != run_id,
                Run.started_at > datetime.now(UTC) - STALE_RUN,
            )
        )
        if other is not None:
            await session.rollback()
            return None
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
    roles = (run.scope or {}).get("roles") or None
    await session.commit()
    return [{"board_id": str(b.id), "ats": b.ats, "roles": roles} for b in boards]


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
