from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from schemas.application import ApplicationOut
from schemas.job import ATS
from schemas.posting import PostingDetail


class ListingRow(BaseModel):
    id: UUID
    title: str
    company: str
    locations: list[str]
    work_mode: str | None
    status: str
    last_posted_at: datetime | None
    platforms: list[ATS]  # boards with an open posting of this listing (the badge)
    postings: int
    role: str  # canonical role
    matched: list[str]  # conditions it met: role, skills, location (F3)
    matched_role: str | None  # the target role it counts under in Jobs by role (U2)
    nice_skills: list[str]  # my nice-to-have skills the job asks for, shown as tags


class ListingPage(BaseModel):
    total: int
    page: int
    page_size: int
    rows: list[ListingRow]


class StatusCounts(BaseModel):
    listings: int = 0
    not_applied: int = 0
    ready_for_review: int = 0
    applying: int = 0
    applied: int = 0
    needs_input: int = 0
    failed: int = 0
    manual_apply: int = 0
    skipped: int = 0


class RoleCounts(StatusCounts):
    role: str | None  # None: listed through Skills or Location only


class ListingSummary(BaseModel):
    """Totals (U1) and Jobs by role (U2) over the listed, not Closed listings."""

    totals: StatusCounts
    by_role: list[RoleCounts]


class AlsoOn(BaseModel):
    posting_id: UUID
    source: ATS
    title: str
    company: str
    locations: list[str]
    posting_url: str
    state: str
    posted_at: datetime | None


class ListingRef(BaseModel):
    id: UUID
    title: str


class ListingDetail(ListingRow):
    skills: list[str]
    seniority: str | None
    remote_regions: list[str]
    primary: PostingDetail | None
    also_on: list[AlsoOn]
    same_company: list[ListingRef]  # merge targets
    application: ApplicationOut | None  # the newest attempt: prepared, waiting or sent
    attempts: int
    apply_blocked: str | None  # why Apply now is not offered, None when it is


class MergeIn(BaseModel):
    other_listing_id: UUID


class SplitOut(BaseModel):
    listing_id: UUID


class DuplicateSide(BaseModel):
    id: UUID
    title: str
    platforms: list[ATS]
    locations: list[str]


class DuplicateOut(BaseModel):
    id: UUID
    company: str
    a: DuplicateSide
    b: DuplicateSide
    title_similarity: float
    description_similarity: float


class DecisionIn(BaseModel):
    decision: Literal["merged", "kept_apart"]
