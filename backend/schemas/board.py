from uuid import UUID

from pydantic import BaseModel, Field

from schemas.job import ATS


class BoardIn(BaseModel):
    ats: ATS
    board_token: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=100)


class BoardOut(BoardIn):
    id: UUID
    origin: str
    active: bool
