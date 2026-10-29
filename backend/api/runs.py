"""Runs (T3 to T5, U6): start a Find only run, read the last run line and the run log."""

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from database.models import Posting, Run
from database.session import Session
from functions.events import send
from pipeline.runs import queue_run
from schemas.run import RunIn, RunOut

router = APIRouter(prefix="/runs", tags=["runs"])


async def extracting(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Posting)
        .where(
            Posting.state == "open",
            Posting.extracted_hash.is_distinct_from(Posting.description_hash),
        )
    )
    return int(count or 0)


def to_out(run: Run, pending: int = 0) -> RunOut:
    return RunOut(
        id=run.id,
        trigger=run.trigger,
        mode=run.mode,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        counts=run.counts or {},
        error=run.error,
        extracting=pending,
    )


@router.post("", response_model=RunOut, status_code=202)
async def start(body: RunIn, session: Session) -> RunOut:
    scope = {"board_ids": [str(b) for b in body.board_ids]} if body.board_ids else {}
    run, created = await queue_run(session, "manual", body.mode, scope)
    if created:
        await send("run/requested", {"run_id": str(run.id)})
    return to_out(run, await extracting(session))


@router.get("/latest", response_model=RunOut | None)
async def latest(session: Session) -> RunOut | None:
    run = await session.scalar(select(Run).order_by(Run.created_at.desc()).limit(1))
    return to_out(run, await extracting(session)) if run else None


@router.get("", response_model=list[RunOut])
async def run_log(session: Session) -> list[RunOut]:
    rows = await session.scalars(select(Run).order_by(Run.created_at.desc()).limit(50))
    return [to_out(r) for r in rows]


@router.get("/{run_id}", response_model=RunOut)
async def one(run_id: str, session: Session) -> RunOut:
    run = await session.get(Run, run_id)
    if run is None:
        raise ApiError(404, "not_found", "No such run")
    return to_out(run)
