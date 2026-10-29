from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

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


class ListingPage(BaseModel):
    total: int
    page: int
    page_size: int
    rows: list[ListingRow]


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
    canonical_role: str
    skills: list[str]
    seniority: str | None
    remote_regions: list[str]
    primary: PostingDetail | None
    also_on: list[AlsoOn]
    same_company: list[ListingRef]  # merge targets


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
