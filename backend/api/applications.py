"""Applications (Apply flow, U14, U15): what is prepared or what was sent, and my answers.

Every press writes the application row first and then sends its event, so apply-listing
reads the new status when it wakes (pipeline/apply/flow.py).
"""

import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from config import get_settings
from database.models import Application, Listing, ResumeVersion
from database.session import Session
from functions.events import send
from pipeline.apply.answers import save_to_bank
from schemas.application import (
    AnswerQuestionsIn,
    AnswersIn,
    ApplicationOut,
    ApplyQueued,
    FieldOut,
    FrozenJd,
    PendingOut,
    ResumeRef,
)

router = APIRouter(tags=["applications"])


def _exists(path: str | None) -> bool:
    return bool(path) and Path(str(path)).exists()


def _inside_data(path: str | None) -> bool:
    root = Path(get_settings().jap_data_dir).resolve()
    return bool(path) and Path(str(path)).resolve().is_relative_to(root) and _exists(path)


async def _listing_to(session: AsyncSession, listing_id: uuid.UUID, status: str) -> None:
    await session.execute(update(Listing).where(Listing.id == listing_id).values(status=status))


async def to_out(session: AsyncSession, app: Application) -> ApplicationOut:
    resume = await session.get(ResumeVersion, app.resume_version_id)
    jd = app.jd_snapshot
    answers = app.answers or {}
    return ApplicationOut(
        id=app.id,
        listing_id=app.listing_id,
        status=app.status,
        mode=app.mode,
        created_at=app.created_at,
        updated_at=app.updated_at,
        resume=ResumeRef(
            id=resume.id, original_name=resume.original_name, uploaded_at=resume.uploaded_at
        )
        if resume
        else None,  # fmt: skip
        jd=FrozenJd(
            title=str(jd.get("title") or ""),
            text=str(jd.get("text") or ""),
            posting_url=str(jd.get("posting_url") or ""),
            source=str(jd.get("source") or ""),
            captured_at=app.captured_at,
        )
        if jd
        else None,
        fields=[
            FieldOut(
                key=f["key"],
                label=f["label"],
                type=f["type"],
                required=bool(f["required"]),
                options=list(f.get("options") or []),
                max_chars=f.get("max_chars"),
                filled_from=str(f.get("filled_from") or ""),
                value=str(answers.get(f["key"], "")),
            )
            for f in app.form_fields or []
        ],
        pending=[PendingOut.model_validate(q) for q in app.pending_questions or []],
        cover_letter_text=app.cover_letter_text,
        error=app.error,
        submitted_at=app.submitted_at,
        email_sent_at=app.email_sent_at,
        has_screenshot=_exists(app.screenshot_path),
        has_trace=_exists(app.trace_path),
    )


async def get_app(session: AsyncSession, app_id: uuid.UUID, *, lock: bool = False) -> Application:
    app = await session.get(Application, app_id, with_for_update=lock)
    if app is None:
        raise ApiError(404, "not_found", "No such application")
    return app


def _require(app: Application, status: str, what: str) -> None:
    if app.status != status:
        raise ApiError(409, "conflict", f"{what} only while {status.replace('_', ' ')}; "
                       f"this application is {app.status.replace('_', ' ')}")  # fmt: skip


def _set_answers(app: Application, given: dict[str, str], allowed: set[str]) -> None:
    unknown = set(given) - allowed
    if unknown:
        raise ApiError(422, "validation", f"Not a field on this form: {', '.join(sorted(unknown))}")
    answers = dict(app.answers or {})
    fields = []
    for f in app.form_fields or []:
        key = f["key"]
        if key in given and given[key].strip() != str(answers.get(key, "")).strip():
            answers[key] = given[key].strip()
            f = {**f, "filled_from": "you" if given[key].strip() else ""}
            if not given[key].strip():
                answers.pop(key, None)
        fields.append(f)
    app.answers = answers
    app.form_fields = fields


@router.get("/applications/{app_id}", response_model=ApplicationOut)
async def one(app_id: uuid.UUID, session: Session) -> ApplicationOut:
    return await to_out(session, await get_app(session, app_id))


@router.put("/applications/{app_id}", response_model=ApplicationOut)
async def edit(app_id: uuid.UUID, body: AnswersIn, session: Session) -> ApplicationOut:
    """Edit answers while Ready for review (C5)."""
    app = await get_app(session, app_id, lock=True)
    _require(app, "ready_for_review", "Answers can be edited")
    editable = {f["key"] for f in app.form_fields or [] if f["type"] != "file"}
    _set_answers(app, body.answers, editable)
    await session.commit()
    await session.refresh(app)
    return await to_out(session, app)


@router.post("/applications/{app_id}/approve", response_model=ApplicationOut)
async def approve(app_id: uuid.UUID, session: Session) -> ApplicationOut:
    """Approve and submit (A4): the attempt moves on to submit."""
    app = await get_app(session, app_id, lock=True)
    _require(app, "ready_for_review", "Approve works")
    answers = app.answers or {}
    missing = [f["label"] for f in app.form_fields or []
               if f["required"] and not str(answers.get(f["key"], "")).strip()]  # fmt: skip
    if missing:
        raise ApiError(422, "validation", f"Required and empty: {'; '.join(missing)}")
    app.status = "applying"
    await _listing_to(session, app.listing_id, "applying")
    await session.commit()
    await send("application/approved", {"application_id": str(app.id)})
    await session.refresh(app)
    return await to_out(session, app)


@router.post("/applications/{app_id}/answer", response_model=ApplicationOut)
async def answer(app_id: uuid.UUID, body: AnswerQuestionsIn, session: Session) -> ApplicationOut:
    """Answer the Needs input questions (U15); the attempt is prepared again with them."""
    app = await get_app(session, app_id, lock=True)
    _require(app, "needs_input", "Answers are taken")
    pending = [q for q in app.pending_questions or [] if not q.get("blocked")]
    if not pending:
        raise ApiError(409, "conflict", "Nothing here can be answered; open the posting or Skip")
    given = {k: v.strip() for k, v in body.answers.items()}
    empty = [q["label"] for q in pending if not given.get(q["key"])]
    if empty:
        raise ApiError(422, "validation", f"Answer every question: {'; '.join(empty)}")
    _set_answers(app, given, {q["key"] for q in pending})
    if body.save_to_bank:
        for q in pending:
            await save_to_bank(session, q["label"], given[q["key"]])
    app.status = "preparing"
    app.pending_questions = [q for q in app.pending_questions or [] if q.get("key") == "captcha"]
    await _listing_to(session, app.listing_id, "applying")
    await session.commit()
    await send("application/answered", {"application_id": str(app.id)})
    await session.refresh(app)
    return await to_out(session, app)


@router.post("/applications/{app_id}/retry", response_model=ApplyQueued, status_code=202)
async def retry(app_id: uuid.UUID, session: Session) -> ApplyQueued:
    """Retry a Failed attempt as a new attempt (U15)."""
    app = await get_app(session, app_id)
    _require(app, "failed", "Retry works")
    newer = await session.scalar(
        select(Application.id).where(
            Application.listing_id == app.listing_id, Application.created_at > app.created_at
        )
    )
    if newer is not None:
        raise ApiError(409, "conflict", "A newer attempt exists for this job")
    await send("listing/apply.requested", {"listing_id": str(app.listing_id), "run_id": None})
    return ApplyQueued(listing_id=app.listing_id, queued=True)


@router.get("/files/{kind}/{app_id}")
async def file(kind: str, app_id: uuid.UUID, session: Session) -> FileResponse:
    """A screenshot or Playwright trace from data/ (U14, U15)."""
    app = await get_app(session, app_id)
    path = {"screenshot": app.screenshot_path, "trace": app.trace_path}.get(kind)
    if kind not in ("screenshot", "trace"):
        raise ApiError(404, "not_found", "No such file kind")
    if not _inside_data(path):
        raise ApiError(404, "not_found", f"No {kind} for this application")
    media = "image/png" if kind == "screenshot" else "application/zip"
    assert path is not None
    return FileResponse(path, media_type=media, filename=Path(path).name)
