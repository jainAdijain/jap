"""Resume versions (T1, U7): upload, list, download, accept the parsed skills."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from api.profile import load_profile
from config import get_settings
from database.models import ResumeVersion
from database.session import Session
from functions.events import send
from pipeline.resume import (
    MAX_BYTES,
    BadResume,
    detect_mime,
    normalize_skills,
    skill_aliases,
    store,
)
from schemas.resume import AcceptSkills, ResumeOut

router = APIRouter(prefix="/resumes", tags=["resumes"])


def to_out(v: ResumeVersion) -> ResumeOut:
    parsed = v.parsed or {}
    status = "failed" if v.parse_error else "parsed" if v.parsed is not None else "parsing"
    return ResumeOut(
        id=v.id,
        original_name=v.original_name,
        mime=v.mime,
        uploaded_at=v.uploaded_at,
        is_active=v.is_active,
        accepted_at=v.accepted_at,
        status=status,
        parse_error=v.parse_error,
        suggested_skills=parsed.get("skills", []),
        titles=parsed.get("titles", []),
        years_experience=parsed.get("years_experience"),
        links_found=parsed.get("links", []),
    )


async def get_version(session: AsyncSession, version_id: uuid.UUID) -> ResumeVersion:
    version = await session.get(ResumeVersion, version_id)
    if version is None:
        raise ApiError(404, "not_found", "No such resume version")
    return version


@router.post("", response_model=ResumeOut, status_code=201)
async def upload(file: UploadFile, session: Session) -> ResumeOut:
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ApiError(413, "too_large", "Resume files are capped at 10 MB")
    try:
        mime = detect_mime(file.filename or "", data)
    except BadResume as exc:
        raise ApiError(422, "validation", str(exc)) from exc

    version_id = uuid.uuid4()
    path, sha = store(Path(get_settings().jap_data_dir), str(version_id), mime, data)
    # The newest upload is the file attached to applications from now on.
    await session.execute(update(ResumeVersion).values(is_active=False))
    session.add(
        ResumeVersion(
            id=version_id,
            file_path=str(path),
            file_sha256=sha,
            original_name=Path(file.filename or "resume").name,
            mime=mime,
            is_active=True,
        )
    )
    await session.commit()
    await send("resume/uploaded", {"resume_version_id": str(version_id)})
    return to_out(await get_version(session, version_id))


@router.get("", response_model=list[ResumeOut])
async def versions(session: Session) -> list[ResumeOut]:
    rows = await session.scalars(select(ResumeVersion).order_by(ResumeVersion.uploaded_at.desc()))
    return [to_out(v) for v in rows]


@router.get("/{version_id}/file")
async def download(version_id: uuid.UUID, session: Session) -> FileResponse:
    v = await get_version(session, version_id)
    return FileResponse(v.file_path, media_type=v.mime, filename=v.original_name)


@router.post("/{version_id}/accept", response_model=ResumeOut)
async def accept(version_id: uuid.UUID, body: AcceptSkills, session: Session) -> ResumeOut:
    """Accept or edit the suggested skills; they replace the profile's skill lists (T1)."""
    v = await get_version(session, version_id)
    if v.parsed is None:
        raise ApiError(409, "wrong_status", "This resume has not been parsed yet")
    aliases = await skill_aliases(session)
    must = normalize_skills(body.must_have_skills, aliases)
    nice = [s for s in normalize_skills(body.nice_to_have_skills, aliases) if s not in must]
    profile = await load_profile(session)
    profile.must_have_skills = must
    profile.nice_to_have_skills = nice
    await session.execute(
        update(ResumeVersion).where(ResumeVersion.id != v.id).values(is_active=False)
    )
    v.is_active = True
    v.accepted_at = datetime.now(UTC)
    await session.commit()
    # The resume_updated run (T1 -> fetch, filter, apply) is wired up with triggers in Phase 5.
    return to_out(await get_version(session, version_id))
