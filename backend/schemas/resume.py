from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ParsedResume(BaseModel):
    """What the resume_parse prompt returns (T1)."""

    skills: list[str]
    titles: list[str]
    years_experience: float | None


class ResumeOut(BaseModel):
    id: UUID
    original_name: str
    mime: str
    uploaded_at: datetime
    is_active: bool
    accepted_at: datetime | None
    status: str  # parsing, parsed, failed
    parse_error: str | None
    suggested_skills: list[str]
    titles: list[str]
    years_experience: float | None
    links_found: list[str]


class AcceptSkills(BaseModel):
    must_have_skills: list[str]
    nice_to_have_skills: list[str]
