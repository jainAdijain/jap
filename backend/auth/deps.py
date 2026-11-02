"""The dependency every non-public route carries (PRD S6, S7): a live signed-in session."""

from typing import Annotated

from fastapi import Depends, Request

from api.errors import ApiError
from auth import sessions
from database.models import AppUser, AuthSession
from database.session import Session


async def current_session(request: Request, session: Session) -> AuthSession:
    row = await sessions.find(session, request, sessions.SIGNED_IN)
    if row is None:
        raise ApiError(401, "signed_out", "Sign in to continue")
    await sessions.touch(session, row)
    return row


async def current_user(
    row: Annotated[AuthSession, Depends(current_session)], session: Session
) -> AppUser:
    user = await session.get(AppUser, row.user_id)
    if user is None or user.disabled_at is not None:
        raise ApiError(401, "signed_out", "Sign in to continue")
    return user


CurrentSession = Annotated[AuthSession, Depends(current_session)]
CurrentUser = Annotated[AppUser, Depends(current_user)]
