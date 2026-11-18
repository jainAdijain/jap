"""Finish yourself: puzzle (JAP-11). The container side of `make finish`.

A form with a picture puzzle (CAPTCHA) is never solved or worked around by JAP. JAP fills in
everything else and stops before Submit. `make finish` runs infra/scripts/finish.py on the
Mac, which asks this module for the form, opens it filled in, in a visible browser, waits
while I solve the puzzle and press Submit, and reports the confirmation back here.

    python -m pipeline.apply.finish list                 jobs waiting for me to finish them
    python -m pipeline.apply.finish export <id>          the form, answers and files, as JSON
    python -m pipeline.apply.finish done <id> [<shot>]   I sent it: the job is Applied

`export` prints answers and contact details, so it is only ever read by finish.py through a
pipe, never shown or logged. File paths are relative to the data folder, which the Mac sees
as data/ (or data-live/).
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import Application, Company, Listing, ResumeVersion
from database.session import get_sessionmaker
from functions.events import send
from pipeline.apply import flow


def _relative(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return str(Path(path).relative_to(get_settings().jap_data_dir))
    except ValueError:
        return None


async def waiting(session: AsyncSession) -> list[dict[str, str]]:
    rows = await session.scalars(
        select(Application)
        .where(Application.status == "needs_input")
        .order_by(Application.created_at)
    )
    out = []
    for app in rows:
        if not flow.finish_only(app.pending_questions):
            continue
        listing = await session.get(Listing, app.listing_id)
        company = await session.get(Company, listing.company_id) if listing else None
        out.append(
            {
                "id": str(app.id),
                "company": company.name if company else "",
                "title": str((app.jd_snapshot or {}).get("title") or ""),
            }
        )
    return out


async def export(session: AsyncSession, app_id: str) -> dict[str, Any]:
    app = await session.get(Application, uuid.UUID(app_id))
    if app is None or app.status != "needs_input" or not flow.finish_only(app.pending_questions):
        raise SystemExit(f"{app_id} is not waiting for you to finish it")
    resume = await session.get(ResumeVersion, app.resume_version_id)
    files: dict[str, dict[str, str | None]] = {}
    for f in app.form_fields or []:
        if f.get("filled_from") == "resume" and resume is not None:
            files[f["key"]] = {
                "path": _relative(resume.file_path),
                "name": resume.original_name,
                "mime": resume.mime,
            }
    if app.cover_letter_path:
        for f in flow.letter_fields(app):
            if f["type"] == "file":
                name = str((app.answers or {}).get(f["key"]) or "Cover-Letter.pdf")
                files[f["key"]] = {
                    "path": _relative(app.cover_letter_path),
                    "name": name,
                    "mime": "application/pdf",
                }
    return {
        "id": str(app.id),
        "url": str((app.jd_snapshot or {}).get("apply_url") or ""),
        "fields": [
            {k: v for k, v in f.items() if k != "filled_from"} for f in app.form_fields or []
        ],
        "answers": {k: str(v) for k, v in (app.answers or {}).items()},
        "files": files,
    }


async def done(session: AsyncSession, app_id: str, screenshot: str | None) -> None:
    shot = str(Path(get_settings().jap_data_dir) / screenshot) if screenshot else None
    try:
        app = await flow.finished(session, app_id, shot)
    except flow.Stop as exc:
        raise SystemExit(str(exc)) from exc
    await send("application/answered", {"application_id": str(app.id)})
    await send("application/submitted", {"application_id": str(app.id)})


async def _main(args: list[str]) -> None:
    async with get_sessionmaker()() as session:
        if args[:1] == ["list"]:
            print(json.dumps(await waiting(session)))
        elif args[:1] == ["export"] and len(args) == 2:
            print(json.dumps(await export(session, args[1]), ensure_ascii=False))
        elif args[:1] == ["done"] and len(args) in (2, 3):
            await done(session, args[1], args[2] if len(args) == 3 else None)
            print("Marked Applied.")
        else:
            raise SystemExit(
                "usage: python -m pipeline.apply.finish list|export <id>|done <id> [<shot>]"
            )


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
