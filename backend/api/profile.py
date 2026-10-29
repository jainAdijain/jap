"""GET, PUT and PATCH /api/profile (U7 to U10)."""

from fastapi import APIRouter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Profile
from database.session import Session
from functions.events import send
from llm.spend import month_spend
from pipeline.resume import normalize_skills, skill_aliases
from schemas.profile import ProfileIn, ProfileOut, ProfileSaved, ProfileSwitches

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
        daily_cap=profile.daily_cap,
        spend=await month_spend(session),
    )
    return ProfileOut.model_validate(data)


@router.get("", response_model=ProfileOut)
async def get_profile(session: Session) -> ProfileOut:
    return await to_out(session, await load_profile(session))


@router.put("", response_model=ProfileSaved)
async def save_profile(body: ProfileIn, session: Session) -> ProfileSaved:
    profile = await load_profile(session)
    aliases = await skill_aliases(session)
    data = body.model_dump(mode="json")
    data["must_have_skills"] = normalize_skills(body.must_have_skills, aliases)
    data["nice_to_have_skills"] = [
        s
        for s in normalize_skills(body.nice_to_have_skills, aliases)
        if s not in data["must_have_skills"]
    ]
    for field, value in data.items():
        setattr(profile, field, value)
    await session.commit()
    await session.refresh(profile)
    # Listings get their match flags from Phase 4 on; until then nothing is listed.
    matching = await session.scalar(text("SELECT count(*) FROM listing WHERE matched IS NOT NULL"))
    await send("profile/saved")
    out = await to_out(session, profile)
    return ProfileSaved(**out.model_dump(), matching_count=int(matching or 0))


@router.patch("", response_model=ProfileOut)
async def flip_switches(body: ProfileSwitches, session: Session) -> ProfileOut:
    profile = await load_profile(session)
    if body.apply_mode is not None:
        profile.apply_mode = body.apply_mode
    if body.daily_run is not None:
        profile.daily_run = body.daily_run
    await session.commit()
    await session.refresh(profile)
    return await to_out(session, profile)
