"""parse-resume (T1): extract text locally, ask the model for suggestions, save them.

The suggestions wait for me in the UI (accept or edit), not in Inngest. Steps return ids,
counts and the redacted text that went to the model, never the resume itself.
"""

import uuid
from pathlib import Path
from typing import Any

import inngest
from sqlalchemy import select

from database.models import Profile, ResumeVersion
from database.session import get_sessionmaker
from functions.client import inngest_client
from llm import PersonalDataBlocked, SpendCapReached, call
from llm.client import MissingApiKey
from llm.redact import find_links, from_resume_text
from pipeline.resume import extract_text, normalize_skills, sensitive_from_profile, skill_aliases
from schemas.resume import ParsedResume


async def _set_error(version_id: str, message: str, *, keep_existing: bool = False) -> None:
    async with get_sessionmaker()() as session:
        version = await session.get(ResumeVersion, uuid.UUID(version_id))
        if version is not None and not (keep_existing and version.parse_error):
            version.parse_error = message
            await session.commit()


async def _failed(ctx: inngest.Context) -> None:
    original = ctx.event.data.get("event")
    data = original.get("data") if isinstance(original, dict) else None
    version_id = data.get("resume_version_id") if isinstance(data, dict) else None
    if isinstance(version_id, str):
        await _set_error(
            version_id, "Parsing failed after retries. Upload the resume again.", keep_existing=True
        )


async def extract_step(version_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        version = await session.get(ResumeVersion, uuid.UUID(version_id))
        if version is None:
            raise inngest.NonRetriableError("resume version not found")
        text = extract_text(Path(version.file_path), version.mime)
        if not text:
            version.parse_error = "No text found in the file (is it a scanned image?)"
            await session.commit()
            raise inngest.NonRetriableError("no text in resume")
        version.text = text
        version.parse_error = None
        await session.commit()
        return {"resume_version_id": version_id, "chars": len(text)}


async def parse_step(version_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        version = await session.get(ResumeVersion, uuid.UUID(version_id))
        if version is None or not version.text:
            raise inngest.NonRetriableError("resume text missing")
        resume_text = version.text
        profile = await session.scalar(select(Profile))
        # Profile contact details first; on a first upload, what the resume shows.
        sensitive = sensitive_from_profile(profile).merge(from_resume_text(resume_text))
        try:
            result = await call(
                session,
                "resume_parse",
                {"resume_text": resume_text},
                ParsedResume,
                sensitive=sensitive,
                generic_redact=frozenset({"resume_text"}),
            )
        except (PersonalDataBlocked, SpendCapReached, MissingApiKey) as exc:
            await _set_error(version_id, str(exc))
            raise inngest.NonRetriableError(str(exc)) from exc
        parsed = result.output
        skills = normalize_skills(parsed.skills, await skill_aliases(session))
        version = await session.get(ResumeVersion, uuid.UUID(version_id))
        assert version is not None
        version.parsed = {
            "skills": skills,
            "titles": [t for t in parsed.titles if "[" not in t],
            "years_experience": parsed.years_experience,
            "links": find_links(resume_text),
        }
        await session.commit()
        return {
            "resume_version_id": version_id,
            "skills_found": len(skills),
            # Shown in the Inngest UI so the redaction can be checked by eye.
            "sent_to_model": result.model_input,
        }


@inngest_client.create_function(
    fn_id="parse-resume",
    trigger=inngest.TriggerEvent(event="resume/uploaded"),
    retries=2,
    on_failure=_failed,
)
async def parse_resume(ctx: inngest.Context) -> dict[str, Any]:
    version_id = str(ctx.event.data["resume_version_id"])
    await ctx.step.run("extract-text", extract_step, version_id)
    return await ctx.step.run("parse", parse_step, version_id)
