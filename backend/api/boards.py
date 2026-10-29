"""Companies to watch (I8, U8): the boards each run fetches, grouped by ATS in the UI.

Detecting a board from a pasted URL (POST /api/boards/detect) arrives with ingestion.
"""

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from api.errors import ApiError
from database.models import WatchedBoard
from database.session import Session
from schemas.board import BoardIn, BoardOut
from schemas.job import ATS

router = APIRouter(prefix="/boards", tags=["boards"])


def to_out(b: WatchedBoard) -> BoardOut:
    return BoardOut(
        id=b.id,
        ats=ATS(b.ats),
        board_token=b.board_token,
        display_name=b.display_name,
        origin=b.origin,
        active=b.active,
    )


@router.get("", response_model=list[BoardOut])
async def boards(session: Session) -> list[BoardOut]:
    rows = await session.scalars(
        select(WatchedBoard).where(WatchedBoard.active).order_by(WatchedBoard.display_name)
    )
    return [to_out(b) for b in rows]


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


@router.delete("/{board_id}", status_code=204)
async def stop_watching(board_id: uuid.UUID, session: Session) -> None:
    board = await session.get(WatchedBoard, board_id)
    if board is None:
        raise ApiError(404, "not_found", "No such board")
    board.active = False  # kept, so its postings keep their board
    await session.commit()
