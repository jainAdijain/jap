"""The canonical Job record (I2): what every source adapter returns before it is stored."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, HttpUrl


class ATS(StrEnum):
    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"


class Salary(BaseModel):
    min: float | None = None
    max: float | None = None
    currency: str | None = None
    period: str | None = None  # year, month, hour


class Job(BaseModel):
    source: ATS
    external_id: str
    company_name: str
    title: str
    locations: list[str]
    department: str | None
    employment_type: str | None
    posted_at: datetime | None
    posting_url: HttpUrl
    apply_url: HttpUrl
    requisition_id: str | None
    description_html: str
    raw_payload: dict[str, Any]
    salary: Salary | None = None  # Ashby compensation, when returned
