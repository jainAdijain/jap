from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ResumeRef(BaseModel):
    id: UUID
    original_name: str
    uploaded_at: datetime


class FrozenJd(BaseModel):
    """The JD as it was when the attempt started (G5)."""

    title: str
    text: str
    posting_url: str
    source: str
    captured_at: datetime | None


class FieldOut(BaseModel):
    key: str
    label: str
    type: str
    required: bool
    options: list[str]
    max_chars: int | None
    # Where the value came from: profile:<field>, resume, answer_bank, drafted, you, or ""
    filled_from: str
    value: str


class PendingOut(BaseModel):
    """A question only I can answer (Needs input, U15)."""

    key: str
    label: str
    reason: str
    options: list[str] = []
    blocked: bool = False  # a CAPTCHA or a file: can't be answered here


class ApplicationOut(BaseModel):
    id: UUID
    listing_id: UUID
    status: str
    mode: str
    created_at: datetime
    updated_at: datetime
    resume: ResumeRef | None
    jd: FrozenJd | None
    fields: list[FieldOut]
    pending: list[PendingOut]
    cover_letter_text: str | None
    error: str | None
    submitted_at: datetime | None
    email_sent_at: datetime | None
    has_screenshot: bool
    has_trace: bool


class AnswersIn(BaseModel):
    """Edited answers while Ready for review (C5), by field key."""

    answers: dict[str, str] = Field(max_length=200)


class AnswerQuestionsIn(AnswersIn):
    """Answers to Needs input questions, and whether to keep them in the answer bank."""

    save_to_bank: bool = True


class ApplyQueued(BaseModel):
    listing_id: UUID
    queued: bool
