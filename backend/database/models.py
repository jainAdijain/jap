"""SQLAlchemy mappings for the tables the app writes so far.

The schema itself lives in migrations/versions (0001_initial); these classes only map it.
Tables used from Phase 4 on (application, ...) get their classes when their code arrives.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base

ATS = ENUM("greenhouse", "lever", "ashby", name="ats", create_type=False)
WORK_MODE = ENUM("remote", "hybrid", "onsite", name="work_mode", create_type=False)
APPLY_MODE = ENUM("review", "automatic", name="apply_mode", create_type=False)
POSTING_STATE = ENUM("open", "closed", name="posting_state", create_type=False)
RUN_TRIGGER = ENUM("resume_updated", "manual", "scheduled", name="run_trigger", create_type=False)
RUN_MODE = ENUM("find_only", "find_and_apply", name="run_mode", create_type=False)
LISTING_STATUS = ENUM(
    "not_applied", "ready_for_review", "applying", "applied", "needs_input", "failed",
    "manual_apply", "skipped", "closed", name="listing_status", create_type=False,
)  # fmt: skip
RUN_STATUS = ENUM("queued", "running", "succeeded", "failed", name="run_status", create_type=False)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )


class Profile(Timestamps, Base):
    __tablename__ = "profile"

    id: Mapped[uuid.UUID] = _id()
    singleton: Mapped[bool] = mapped_column(Boolean, server_default="true")
    full_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    links: Mapped[list[dict[str, str]]] = mapped_column(JSONB, server_default="[]")
    target_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    must_have_skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    nice_to_have_skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    remote_regions: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    match_rule: Mapped[list[list[str]]] = mapped_column(
        JSONB, server_default='[["role"], ["skills"], ["location"]]'
    )
    work_modes: Mapped[list[str]] = mapped_column(
        ARRAY(WORK_MODE), server_default="{remote,hybrid,onsite}"
    )
    excluded_companies: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    excluded_title_words: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    tone_note: Mapped[str | None] = mapped_column(Text)
    apply_mode: Mapped[str] = mapped_column(APPLY_MODE, server_default="review")
    daily_run: Mapped[bool] = mapped_column(Boolean, server_default="false")
    daily_cap: Mapped[int] = mapped_column(Integer, server_default="20")


class ResumeVersion(Timestamps, Base):
    __tablename__ = "resume_version"

    id: Mapped[uuid.UUID] = _id()
    file_path: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parse_error: Mapped[str | None] = mapped_column(Text)
    accepted_at: Mapped[datetime | None]
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="false")
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WatchedBoard(Timestamps, Base):
    __tablename__ = "watched_board"

    id: Mapped[uuid.UUID] = _id()
    ats: Mapped[str] = mapped_column(ATS)
    board_token: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    origin: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_checked_at: Mapped[datetime | None]
    last_ok: Mapped[bool | None]


class Company(Timestamps, Base):
    """Canonical company: one row however many boards and spellings it has."""

    __tablename__ = "company"

    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(Text)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)
    careers_domain: Mapped[str | None] = mapped_column(Text, unique=True)


class CompanyAlias(Timestamps, Base):
    __tablename__ = "company_alias"

    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    origin: Mapped[str] = mapped_column(Text)  # auto or user


class Listing(Timestamps, Base):
    """One company plus one role (D1 to D4); what the dashboard shows."""

    __tablename__ = "listing"

    id: Mapped[uuid.UUID] = _id()
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    canonical_role: Mapped[str] = mapped_column(Text)
    title_display: Mapped[str] = mapped_column(Text)
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    work_mode: Mapped[str | None] = mapped_column(WORK_MODE)
    remote_regions: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    skills: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    seniority: Mapped[str | None] = mapped_column(Text)
    primary_posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(LISTING_STATUS, server_default="not_applied")
    matched: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # null = not listed (F3)
    matched_role: Mapped[str | None] = mapped_column(Text)  # target role it counts under (U2)
    first_posted_at: Mapped[datetime | None]
    last_posted_at: Mapped[datetime | None]
    applied_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]


class DedupOverride(Timestamps, Base):
    """A merge or split I chose (D5), remembered by posting so it survives later runs."""

    __tablename__ = "dedup_override"

    id: Mapped[uuid.UUID] = _id()
    kind: Mapped[str] = mapped_column(Text)  # merge or split
    posting_source: Mapped[str] = mapped_column(ATS)
    posting_external_id: Mapped[str] = mapped_column(Text)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class PossibleDuplicate(Timestamps, Base):
    """A borderline pair for me to merge or keep apart (Near match)."""

    __tablename__ = "possible_duplicate"

    id: Mapped[uuid.UUID] = _id()
    kind: Mapped[str] = mapped_column(Text)  # company or listing
    a_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    b_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB)
    decision: Mapped[str] = mapped_column(Text, server_default="open")


class Posting(Timestamps, Base):
    """One job found on one board (I2, I5, I6)."""

    __tablename__ = "posting"

    id: Mapped[uuid.UUID] = _id()
    source: Mapped[str] = mapped_column(ATS)
    external_id: Mapped[str] = mapped_column(Text)
    watched_board_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    listing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    company_name_raw: Mapped[str] = mapped_column(Text)
    title_raw: Mapped[str] = mapped_column(Text)
    canonical_role: Mapped[str | None] = mapped_column(Text)
    requisition_id: Mapped[str | None] = mapped_column(Text)
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    department: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None]
    posting_url: Mapped[str] = mapped_column(Text)
    apply_url: Mapped[str] = mapped_column(Text)
    description_html: Mapped[str] = mapped_column(Text)
    description_text: Mapped[str] = mapped_column(Text)
    description_hash: Mapped[str] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extracted_hash: Mapped[str | None] = mapped_column(Text)
    # embedding (vector(768)) is written and read with plain SQL in pipeline/enrich.py.
    embedded_hash: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(POSTING_STATE, server_default="open")
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    closed_at: Mapped[datetime | None]


class Run(Timestamps, Base):
    """One pipeline run (T4, T5)."""

    __tablename__ = "run"

    id: Mapped[uuid.UUID] = _id()
    trigger: Mapped[str] = mapped_column(RUN_TRIGGER)
    mode: Mapped[str] = mapped_column(RUN_MODE)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(RUN_STATUS, server_default="queued")
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    inngest_run_id: Mapped[str | None] = mapped_column(Text)


class AnswerBank(Timestamps, Base):
    __tablename__ = "answer_bank"

    id: Mapped[uuid.UUID] = _id()
    question_pattern: Mapped[str] = mapped_column(Text)
    question_norm: Mapped[str] = mapped_column(Text, unique=True)
    answer: Mapped[str] = mapped_column(Text)
    last_used_at: Mapped[datetime | None]


class SkillAlias(Timestamps, Base):
    __tablename__ = "skill_alias"

    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical: Mapped[str] = mapped_column(Text)


class LlmCall(Timestamps, Base):
    __tablename__ = "llm_call"

    id: Mapped[uuid.UUID] = _id()
    purpose: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_sha256: Mapped[str] = mapped_column(Text)
    blocked: Mapped[bool] = mapped_column(Boolean, server_default="false")
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
