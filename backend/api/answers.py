"""Answer bank (U9): saved answers to application form questions."""

import uuid

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from database.models import AnswerBank
from database.session import Session
from pipeline.apply.answers import normalize_question
from schemas.answer import AnswerIn, AnswerOut

router = APIRouter(prefix="/answers", tags=["answers"])


def to_out(a: AnswerBank) -> AnswerOut:
    return AnswerOut(id=a.id, question=a.question_pattern, answer=a.answer)


async def _get(session: AsyncSession, answer_id: uuid.UUID) -> AnswerBank:
    row = await session.get(AnswerBank, answer_id)
    if row is None:
        raise ApiError(404, "not_found", "No such answer")
    return row


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(409, "conflict", "That question is already in the answer bank") from exc


@router.get("", response_model=list[AnswerOut])
async def answers(session: Session) -> list[AnswerOut]:
    rows = await session.scalars(select(AnswerBank).order_by(AnswerBank.question_pattern))
    return [to_out(a) for a in rows]


@router.post("", response_model=AnswerOut, status_code=201)
async def add(body: AnswerIn, session: Session) -> AnswerOut:
    row = AnswerBank(
        question_pattern=body.question.strip(),
        question_norm=normalize_question(body.question),
        answer=body.answer.strip(),
    )
    session.add(row)
    await _commit(session)
    await session.refresh(row)
    return to_out(row)


@router.put("/{answer_id}", response_model=AnswerOut)
async def edit(answer_id: uuid.UUID, body: AnswerIn, session: Session) -> AnswerOut:
    row = await _get(session, answer_id)
    row.question_pattern = body.question.strip()
    row.question_norm = normalize_question(body.question)
    row.answer = body.answer.strip()
    await _commit(session)
    await session.refresh(row)
    return to_out(row)


@router.delete("/{answer_id}", status_code=204)
async def remove(answer_id: uuid.UUID, session: Session) -> None:
    await session.delete(await _get(session, answer_id))
    await session.commit()
