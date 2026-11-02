"""Sessions and the cookie that carries them (PRD S5).

The browser holds a random token in an HttpOnly cookie; the database holds only its SHA-256.
A session is either signed_in, or two_step: the password was right and the code is still to
come (5 minutes, 5 tries). A signed-in session ends 7 days after it was last used or 30 days
after sign-in, whichever comes first.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import AppUser, AuthSession

SIGNED_IN = "signed_in"
TWO_STEP = "two_step"
TWO_STEP_MINUTES = 5
TWO_STEP_TRIES = 5
TOUCH_EVERY = timedelta(minutes=1)


def cookie_name(stage: str) -> str:
    # __Host- makes the browser insist on Secure, path / and no Domain: one hostname only.
    base = "jap_session" if stage == SIGNED_IN else "jap_two_step"
    return f"__Host-{base}" if get_settings().jap_cookie_secure else base


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


async def start(
    session: AsyncSession, user: AppUser, stage: str, request: Request, response: Response
) -> AuthSession:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    life = (
        timedelta(days=settings.jap_session_max_days)
        if stage == SIGNED_IN
        else timedelta(minutes=TWO_STEP_MINUTES)
    )
    row = AuthSession(
        user_id=user.id,
        token_hash=token_hash(token),
        stage=stage,
        expires_at=now() + life,
        last_seen_at=now(),
        user_agent=(request.headers.get("user-agent") or "")[:300] or None,
    )
    session.add(row)
    await session.flush()
    response.set_cookie(
        cookie_name(stage),
        token,
        max_age=int(life.total_seconds()),
        path="/",
        secure=settings.jap_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return row


def clear_cookie(response: Response, stage: str) -> None:
    settings = get_settings()
    response.delete_cookie(
        cookie_name(stage), path="/", secure=settings.jap_cookie_secure, httponly=True,
        samesite="lax",
    )  # fmt: skip


def _alive(row: AuthSession) -> bool:
    t = now()
    if row.expires_at <= t:
        return False
    if row.stage == SIGNED_IN:
        idle = timedelta(days=get_settings().jap_session_idle_days)
        return row.last_seen_at + idle > t
    return True


async def find(session: AsyncSession, request: Request, stage: str) -> AuthSession | None:
    """The live session this request's cookie names, or None."""
    token = request.cookies.get(cookie_name(stage))
    if not token:
        return None
    row = await session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash(token), AuthSession.stage == stage
        )
    )
    if row is None:
        return None
    if not _alive(row):
        await session.delete(row)
        await session.commit()
        return None
    return row


async def touch(session: AsyncSession, row: AuthSession) -> None:
    if now() - row.last_seen_at >= TOUCH_EVERY:
        row.last_seen_at = now()
        await session.commit()


async def end_all(session: AsyncSession, user_id: object, keep: AuthSession | None = None) -> None:
    query = delete(AuthSession).where(AuthSession.user_id == user_id)
    if keep is not None:
        query = query.where(AuthSession.id != keep.id)
    await session.execute(query)


async def sweep(session: AsyncSession) -> None:
    """Drop sessions past their absolute end (idle ones go when next looked up)."""
    await session.execute(delete(AuthSession).where(AuthSession.expires_at <= now()))
