from datetime import datetime
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
    last_checked_at: datetime | None = None
    last_ok: bool | None = None


class LimitSend(BaseModel):
    """One job sent inside a company's window: the log behind its limit (V10)."""

    title: str
    sent_at: datetime
    status: str  # applied, or failed when the employer never confirmed it


class CompanyLimitOut(BaseModel):
    """A company's own cap on applications from one person, read off its form (V9, V10)."""

    company: str
    limit: int
    days: int
    sent: list[LimitSend]  # inside the current window, oldest first
    opens_at: datetime | None  # when it takes applications again; None = open now
    opens_on: str | None  # the same in my time zone, e.g. "18 Jan 2027, 6:02 PM"


class UnfollowEffect(BaseModel):
    """What stopping following a company does (I15), counted in jobs as Jobs shows them."""

    display_name: str
    hidden: int  # Not applied jobs that leave the Jobs page
    kept: int  # jobs with an application, which stay in my history
    unread: int  # job descriptions the AI will no longer read


class DetectIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class DetectOut(BaseModel):
    ats: ATS
    board_token: str
    display_name: str
    job_count: int
    already_watched: bool
