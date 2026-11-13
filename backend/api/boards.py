"""Companies to watch (I8, I9, U8): the boards each run fetches, grouped by ATS in the UI.

POST /api/boards/detect turns a pasted board URL or a company name into (ATS, token) and
checks the board answers; nothing is saved until the UI then POSTs /api/boards.

DELETE stops following: the board is kept but switched off, and its open postings are
closed as unfollowed (I10 to I15, pipeline/unfollow.py). POST of the same company switches
the same board back on; nothing is fetched until the next search (I16).
"""

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from api.errors import ApiError
from database.models import WatchedBoard
from database.session import Session
from pipeline.unfollow import effect, unfollow
from schemas.board import BoardIn, BoardOut, DetectIn, DetectOut, UnfollowEffect
from schemas.job import ATS
from sources import base
from sources.detect import NotABoard, detect

router = APIRouter(prefix="/boards", tags=["boards"])


def to_out(b: WatchedBoard) -> BoardOut:
    return BoardOut(
        id=b.id,
        ats=ATS(b.ats),
        board_token=b.board_token,
        display_name=b.display_name,
        origin=b.origin,
        active=b.active,
        last_checked_at=b.last_checked_at,
        last_ok=b.last_ok,
    )


@router.get("", response_model=list[BoardOut])
async def boards(session: Session) -> list[BoardOut]:
    rows = await session.scalars(
        select(WatchedBoard).where(WatchedBoard.active).order_by(WatchedBoard.display_name)
    )
    return [to_out(b) for b in rows]


@router.post("/detect", response_model=DetectOut)
async def detect_board(body: DetectIn, session: Session) -> DetectOut:
    try:
        async with base.client() as http:
            found = await detect(http, body.text)
    except NotABoard as exc:
        raise ApiError(422, "not_a_board", str(exc)) from exc
    watched = await session.scalar(
        select(WatchedBoard.id).where(
            WatchedBoard.ats == found.ats.value,
            WatchedBoard.board_token == found.board_token,
            WatchedBoard.active,
        )
    )
    return DetectOut(
        ats=found.ats,
        board_token=found.board_token,
        display_name=found.display_name,
        job_count=found.job_count,
        already_watched=watched is not None,
    )


@router.post("", response_model=BoardOut, status_code=201)
async def add(body: BoardIn, session: Session) -> BoardOut:
    token = body.board_token.lower()
    board = await session.scalar(
        select(WatchedBoard).where(WatchedBoard.ats == body.ats, WatchedBoard.board_token == token)
    )
    if board is not None and board.active:
        raise ApiError(409, "conflict", f"{body.display_name} is already watched on {body.ats}")
    if board is None:
        board = WatchedBoard(
            ats=body.ats, board_token=token, display_name=body.display_name, origin="user"
        )
        session.add(board)
    else:
        board.active = True
        board.display_name = body.display_name
    await session.commit()
    await session.refresh(board)
    return to_out(board)


@router.get("/{board_id}/unfollow", response_model=UnfollowEffect)
async def unfollow_effect(board_id: uuid.UUID, session: Session) -> UnfollowEffect:
    """What stopping following would do, for the confirmation (I15). Changes nothing."""
    board = await session.get(WatchedBoard, board_id)
    if board is None:
        raise ApiError(404, "not_found", "No such board")
    found = await effect(session, board_id)
    return UnfollowEffect(
        display_name=board.display_name,
        hidden=found.hidden,
        kept=found.kept,
        unread=found.unread,
    )


@router.delete("/{board_id}", status_code=204)
async def stop_watching(board_id: uuid.UUID, session: Session) -> None:
    """Stop following: the board is kept (its postings keep their board) and its open
    postings are closed as unfollowed. Safe to repeat."""
    if await unfollow(session, board_id) is None:
        raise ApiError(404, "not_found", "No such board")
