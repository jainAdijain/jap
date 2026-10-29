"""Hardening on the Status card (Phase 8): the daily backup and the weekly form test.

GET /api/ops shows the last backup and each ATS's last form test; the two POSTs start a
backup or a form test now (the same Inngest functions the schedules run).
"""

import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from api.errors import ApiError
from config import get_settings
from database.models import FormCheck
from database.session import Session
from functions.events import send
from pipeline import backup, form_check
from schemas.ops import BackupOut, FormCheckOut, Ops

router = APIRouter(prefix="/ops", tags=["ops"])


def _check_out(row: FormCheck) -> FormCheckOut:
    return FormCheckOut(
        id=row.id,
        ats=row.ats,
        ok=row.ok,
        fields=row.fields,
        required=row.required,
        url=row.url,
        error=row.error,
        has_screenshot=bool(row.screenshot_path) and Path(str(row.screenshot_path)).exists(),
        checked_at=row.created_at,
    )


@router.get("", response_model=Ops)
async def ops(session: Session) -> Ops:
    files = backup.backups()
    last = files[0] if files else None
    return Ops(
        last_backup=BackupOut(file=last.path.name, at=last.at, bytes=last.size) if last else None,
        backups=len(files),
        backup_today=backup.has_today(),
        form_checks=[_check_out(r) for r in await form_check.latest(session)],
    )


@router.post("/backup", status_code=202)
async def backup_now() -> dict[str, bool]:
    await send("backup/requested")
    return {"queued": True}


@router.post("/form-check", status_code=202)
async def form_check_now() -> dict[str, bool]:
    await send("form-check/requested")
    return {"queued": True}


@router.get("/form-check/{check_id}/screenshot")
async def form_check_screenshot(check_id: uuid.UUID, session: Session) -> FileResponse:
    row = await session.get(FormCheck, check_id)
    path = _inside_data(row.screenshot_path if row else None)
    if path is None:
        raise ApiError(404, "not_found", "No screenshot for this form test")
    return FileResponse(path, media_type="image/png")


def _inside_data(path: str | None) -> Path | None:
    if not path:
        return None
    root = Path(get_settings().jap_data_dir).resolve()
    resolved = Path(path).resolve()
    return resolved if resolved.is_relative_to(root) and resolved.exists() else None
