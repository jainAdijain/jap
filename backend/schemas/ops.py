from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class BackupOut(BaseModel):
    file: str
    at: datetime
    bytes: int


class FormCheckOut(BaseModel):
    id: UUID
    ats: str
    ok: bool
    fields: int
    required: int
    url: str | None
    error: str | None
    has_screenshot: bool
    checked_at: datetime


class Ops(BaseModel):
    """The Hardening part of the Status card: backups and the weekly form test."""

    last_backup: BackupOut | None
    backups: int
    backup_today: bool
    form_checks: list[FormCheckOut]
