"""My profile: what the UI reads and saves (U7 to U10)."""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

WorkMode = Literal["remote", "hybrid", "onsite"]
Condition = Literal["role", "skills", "location"]
ApplyMode = Literal["review", "automatic"]


class Link(BaseModel):
    label: str = Field(max_length=40)
    url: HttpUrl


def _clean(items: list[str]) -> list[str]:
    """Trim, drop blanks and case-insensitive duplicates, keep order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = " ".join(item.split())
        if value and value.lower() not in seen:
            seen.add(value.lower())
            out.append(value)
    return out


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)
    links: list[Link] = []
    target_roles: list[str] = []
    must_have_skills: list[str] = []
    nice_to_have_skills: list[str] = []
    locations: list[str] = []
    remote_regions: list[str] = []
    match_rule: list[list[Condition]] = [["role"], ["skills"], ["location"]]
    work_modes: list[WorkMode] = ["remote", "hybrid", "onsite"]
    excluded_companies: list[str] = []
    excluded_title_words: list[str] = []
    tone_note: str | None = Field(default=None, max_length=500)

    @field_validator(
        "target_roles",
        "locations",
        "remote_regions",
        "excluded_companies",
        "excluded_title_words",
    )
    @classmethod
    def clean_list(cls, v: list[str]) -> list[str]:
        return _clean(v)

    @field_validator("must_have_skills", "nice_to_have_skills")
    @classmethod
    def clean_skills(cls, v: list[str]) -> list[str]:
        return _clean([s.lower() for s in v])

    @field_validator("work_modes")
    @classmethod
    def at_least_one_mode(cls, v: list[WorkMode]) -> list[WorkMode]:
        if not v:
            raise ValueError("Tick at least one work mode")
        return list(dict.fromkeys(v))

    @field_validator("match_rule")
    @classmethod
    def valid_rule(cls, v: list[list[Condition]]) -> list[list[Condition]]:
        combos = [sorted(set(c)) for c in v if c]
        if not combos:
            raise ValueError("The match rule needs at least one combination")
        unique: list[list[Condition]] = []
        for c in combos:
            if c not in unique:
                unique.append(c)
        return unique

    @field_validator("full_name", "phone", "address", "tone_note")
    @classmethod
    def blank_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


class Spend(BaseModel):
    month_usd: float
    cap_usd: float
    reached: bool


class ProfileOut(ProfileIn):
    email: str | None = None  # stored values are returned as saved
    apply_mode: ApplyMode
    daily_run: bool
    daily_cap: int
    spend: Spend


class ProfileSaved(ProfileOut):
    matching_count: int


class MatchPreview(BaseModel):
    matching_count: int


class ProfileSwitches(BaseModel):
    apply_mode: ApplyMode | None = None
    daily_run: bool | None = None
