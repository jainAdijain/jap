from uuid import UUID

from pydantic import BaseModel, Field


class AnswerIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=5000)


class AnswerOut(AnswerIn):
    id: UUID
