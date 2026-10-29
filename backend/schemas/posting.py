from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from schemas.extract import Extracted
from schemas.job import ATS


class PostingRow(BaseModel):
    id: UUID
    source: ATS
    company: str
    title: str
    locations: list[str]
    posted_at: datetime | None
    state: str
    extracted: bool
    work_mode: str | None
    first_seen_at: datetime


class PostingPage(BaseModel):
    total: int
    page: int
    page_size: int
    rows: list[PostingRow]


class PostingDetail(PostingRow):
    department: str | None
    employment_type: str | None
    requisition_id: str | None
    posting_url: str
    apply_url: str
    description_text: str
    fields: Extracted | None
    closed_at: datetime | None
