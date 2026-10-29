"""SQLAlchemy mappings for the tables the app writes so far.

The schema itself lives in migrations/versions (0001_initial); these classes only map it.
Tables used from Phase 2 on (company, listing, posting, application, run, ...) get their
classes when their code arrives.
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
