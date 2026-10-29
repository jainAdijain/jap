"""GET, PUT and PATCH /api/profile (U7 to U10)."""

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Profile
from database.session import Session
from functions.events import send
from llm.spend import month_spend
from pipeline.matching import Criteria, preview_counts, refilter
from pipeline.resume import normalize_skills, skill_aliases
from schemas.profile import MatchPreview, ProfileIn, ProfileOut, ProfileSaved, ProfileSwitches

router = APIRouter(prefix="/profile", tags=["profile"])

FIELDS = list(ProfileIn.model_fields)


async def load_profile(session: AsyncSession) -> Profile:
    """The single profile row, created empty the first time."""
    profile: Profile | None = await session.scalar(select(Profile))
    if profile is None:
        profile = Profile()
        session.add(profile)
        await session.commit()
    await session.refresh(profile)
    return profile


async def to_out(session: AsyncSession, profile: Profile) -> ProfileOut:
    data = {f: getattr(profile, f) for f in FIELDS}
    data.update(
        apply_mode=profile.apply_mode,
        daily_run=profile.daily_run,
        daily_run_at=profile.daily_run_at,
        daily_cap=profile.daily_cap,
        spend=await month_spend(session),
    )
    return ProfileOut.model_validate(data)


@router.get("", response_model=ProfileOut)
async def get_profile(session: Session) -> ProfileOut:
    return await to_out(session, await load_profile(session))


async def normalized(session: AsyncSession, body: ProfileIn) -> dict[str, object]:
    """The profile as it is stored: skills through skill_alias, nice-to-have minus must-have."""
    aliases = await skill_aliases(session)
    data = body.model_dump(mode="json")
    data["must_have_skills"] = normalize_skills(body.must_have_skills, aliases)
    data["nice_to_have_skills"] = [
        s
        for s in normalize_skills(body.nice_to_have_skills, aliases)
        if s not in data["must_have_skills"]
    ]
    return data


@router.put("", response_model=ProfileSaved)
async def save_profile(body: ProfileIn, session: Session) -> ProfileSaved:
    """Save, then re-filter the stored listings at once (T2, F1). profile/saved then starts
    a fetch in the background (profile-saved, debounced), so new matching jobs follow."""
    profile = await load_profile(session)
    for field, value in (await normalized(session, body)).items():
        setattr(profile, field, value)
    await session.flush()
    matching = await refilter(session, Criteria.of(profile))
    await session.commit()
    await session.refresh(profile)
    await send("profile/saved")
    out = await to_out(session, profile)
    return ProfileSaved(**out.model_dump(), matching_count=matching)


@router.post("/preview", response_model=MatchPreview)
async def preview(body: ProfileIn, session: Session) -> MatchPreview:
    """How many listings these settings would list, before saving (U10). Saves nothing."""
    data = await normalized(session, body)
    criteria = Criteria.of(ProfileIn.model_validate(data))
    counts = await preview_counts(session, criteria)
    return MatchPreview(
        matching_count=counts.pop("matching"),
        by_condition={
            "role": counts["role"],
            "skills": counts["skills"],
            "location": counts["location"],
        },  # fmt: skip
    )


@router.patch("", response_model=ProfileOut)
async def flip_switches(body: ProfileSwitches, session: Session) -> ProfileOut:
    profile = await load_profile(session)
    if body.apply_mode is not None:
        profile.apply_mode = body.apply_mode
    if body.daily_run is not None:
        profile.daily_run = body.daily_run
    if body.daily_run_at is not None:
        profile.daily_run_at = body.daily_run_at.replace(second=0, microsecond=0, tzinfo=None)
    await session.commit()
    await session.refresh(profile)
    return await to_out(session, profile)
