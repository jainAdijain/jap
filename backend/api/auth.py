"""Sign-in, sign-up, password reset and two-step sign-in (PRD S1 to S9, U21).

These routes are the only ones besides /api/health that answer without a session. The rest of
/api carries the CurrentUser dependency (main.py).
"""

import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ApiError
from auth import passwords, sessions, totp
from auth.deps import CurrentSession, CurrentUser
from config import get_settings
from database.models import AppUser, PasswordReset
from database.session import Session
from pipeline.notify import EmailNotSent, Mail
from pipeline.notify import send as send_mail
from schemas.auth import (
    AuthState,
    Code,
    Credentials,
    Forgot,
    Password,
    PasswordChange,
    RecoveryCodes,
    Reset,
    SignInResult,
    TwoStepSetup,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("jap.auth")

MAX_FAILED = 5
LOCK_MINUTES = 15
RESET_MINUTES = 30
RESET_EVERY = timedelta(minutes=2)
WRONG = "Wrong email or password"
SIGN_UP_CLOSED = "Sign-up isn't open. Ask the JAP admin for an invite."
ACCOUNT_OFF = "This account is turned off. Ask the JAP admin."
EXPIRED_LINK = "This link has expired or was already used. Ask for a new one."
SIGN_UP_LOCK = 7_342_001  # pg_advisory_xact_lock key: one sign-up at a time


def user_out(user: AppUser) -> UserOut:
    return UserOut(
        email=user.email,
        is_admin=user.is_admin,
        two_step=user.totp_secret is not None,
        recovery_codes_left=len(user.recovery_codes or []),
    )


async def _any_account(session: AsyncSession) -> bool:
    return await session.scalar(select(AppUser.id).limit(1)) is not None


async def _by_email(session: AsyncSession, email: str) -> AppUser | None:
    return await session.scalar(select(AppUser).where(func.lower(AppUser.email) == email.lower()))


def _locked(user: AppUser) -> None:
    if user.locked_until is not None and user.locked_until > sessions.now():
        left = max(1, round((user.locked_until - sessions.now()).total_seconds() / 60))
        raise ApiError(429, "locked", f"Too many tries. Try again in {left} min.")


async def _failed(session: AsyncSession, user: AppUser) -> None:
    user.failed_sign_ins += 1
    if user.failed_sign_ins >= MAX_FAILED:
        user.failed_sign_ins = 0
        user.locked_until = sessions.now() + timedelta(minutes=LOCK_MINUTES)
    await session.commit()


def _new_password(password: str) -> None:
    problem = passwords.problem(password)
    if problem:
        raise ApiError(422, "weak_password", problem)


@router.get("/state", response_model=AuthState)
async def state(request: Request, session: Session) -> AuthState:
    has_accounts = await _any_account(session)
    row = await sessions.find(session, request, sessions.SIGNED_IN)
    user = await session.get(AppUser, row.user_id) if row else None
    pending = await sessions.find(session, request, sessions.TWO_STEP)
    return AuthState(
        has_accounts=has_accounts,
        sign_up_open=not has_accounts,
        user=user_out(user) if user else None,
        two_step_pending=user is None and pending is not None,
    )


@router.post("/sign-up", response_model=SignInResult, status_code=201)
async def sign_up(
    body: Credentials, request: Request, response: Response, session: Session
) -> SignInResult:
    """Create an account (S1). Step 1: only the first one, which becomes the admin.

    Invites and open sign-up (S11, S12) come with each user's own data in step 2.
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SIGN_UP_LOCK})
    if await _any_account(session):
        raise ApiError(409, "sign_up_closed", SIGN_UP_CLOSED)
    allowed = get_settings().jap_admin_email.strip()
    if allowed and allowed.lower() != body.email.lower():
        raise ApiError(403, "not_admin_email", "This address can't create the first JAP account")
    _new_password(body.password)
    user = AppUser(
        email=body.email,
        password_hash=await passwords.hash_password(body.password),
        is_admin=True,
        email_verified_at=sessions.now(),
    )
    session.add(user)
    await session.flush()
    await sessions.start(session, user, sessions.SIGNED_IN, request, response)
    await session.commit()
    return SignInResult(signed_in=True, two_step=False, user=user_out(user))


@router.post("/sign-in", response_model=SignInResult)
async def sign_in(
    body: Credentials, request: Request, response: Response, session: Session
) -> SignInResult:
    user = await _by_email(session, body.email)
    if user is None:
        await passwords.verify_password(body.password, passwords.DUMMY_HASH)
        raise ApiError(401, "wrong_credentials", WRONG)
    _locked(user)
    if not await passwords.verify_password(body.password, user.password_hash):
        await _failed(session, user)
        raise ApiError(401, "wrong_credentials", WRONG)
    if user.disabled_at is not None:
        raise ApiError(403, "account_off", ACCOUNT_OFF)
    await sessions.sweep(session)
    if user.totp_secret is not None:
        await sessions.start(session, user, sessions.TWO_STEP, request, response)
        await session.commit()
        return SignInResult(signed_in=False, two_step=True, user=None)
    user.failed_sign_ins = 0
    user.locked_until = None
    await sessions.start(session, user, sessions.SIGNED_IN, request, response)
    await session.commit()
    return SignInResult(signed_in=True, two_step=False, user=user_out(user))


def _use_code(user: AppUser, code: str) -> bool:
    """True when code is the app's code now or an unused recovery code (used up here)."""
    assert user.totp_secret is not None
    step = totp.matching_step(user.totp_secret, code, user.totp_last_step)
    if step is not None:
        user.totp_last_step = step
        return True
    hashed = totp.recovery_hash(code)
    codes = list(user.recovery_codes or [])
    if hashed in codes:
        codes.remove(hashed)
        user.recovery_codes = codes
        return True
    return False


@router.post("/sign-in/code", response_model=SignInResult)
async def sign_in_code(
    body: Code, request: Request, response: Response, session: Session
) -> SignInResult:
    """The second step (S3): the authenticator code, or a recovery code."""
    pending = await sessions.find(session, request, sessions.TWO_STEP)
    if pending is None:
        raise ApiError(401, "two_step_expired", "Sign-in timed out. Enter your password again.")
    user = await session.get(AppUser, pending.user_id)
    if user is None or user.totp_secret is None:
        raise ApiError(401, "two_step_expired", "Sign-in timed out. Enter your password again.")
    _locked(user)
    if not _use_code(user, body.code):
        pending.code_tries += 1
        if pending.code_tries >= sessions.TWO_STEP_TRIES:
            await session.delete(pending)
            sessions.clear_cookie(response, sessions.TWO_STEP)
        await _failed(session, user)
        raise ApiError(401, "wrong_code", "That code didn't work")
    await session.delete(pending)
    sessions.clear_cookie(response, sessions.TWO_STEP)
    user.failed_sign_ins = 0
    user.locked_until = None
    await sessions.start(session, user, sessions.SIGNED_IN, request, response)
    await session.commit()
    return SignInResult(signed_in=True, two_step=False, user=user_out(user))


@router.post("/sign-out", status_code=204)
async def sign_out(request: Request, response: Response, session: Session) -> None:
    for stage in (sessions.SIGNED_IN, sessions.TWO_STEP):
        row = await sessions.find(session, request, stage)
        if row is not None:
            await session.delete(row)
        sessions.clear_cookie(response, stage)
    await session.commit()


@router.post("/sign-out-everywhere", status_code=204)
async def sign_out_everywhere(user: CurrentUser, response: Response, session: Session) -> None:
    await sessions.end_all(session, user.id)
    await session.commit()
    sessions.clear_cookie(response, sessions.SIGNED_IN)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return user_out(user)


@router.post("/forgot", status_code=202)
async def forgot(body: Forgot, session: Session) -> dict[str, bool]:
    """Email a reset link to the account's address (S4). The answer is the same for any address."""
    user = await _by_email(session, body.email)
    if user is None:
        return {"queued": True}
    last = await session.scalar(
        select(func.max(PasswordReset.created_at)).where(PasswordReset.user_id == user.id)
    )
    if last is not None and sessions.now() - last < RESET_EVERY:
        return {"queued": True}
    token = secrets.token_urlsafe(32)
    session.add(
        PasswordReset(
            user_id=user.id,
            token_hash=sessions.token_hash(token),
            expires_at=sessions.now() + timedelta(minutes=RESET_MINUTES),
        )
    )
    await session.commit()
    link = f"{get_settings().jap_ui_url.rstrip('/')}/?reset={token}"
    mail = Mail(
        to=user.email,
        subject="Reset your JAP password",
        body=(
            "Someone (hopefully you) asked to reset the password for your JAP account.\n\n"
            f"Set a new password here within {RESET_MINUTES} minutes:\n{link}\n\n"
            "The link works once. Setting a new password signs out every browser.\n"
            "If you didn't ask for this, ignore this email; your password stays the same.\n"
        ),
    )
    try:
        await send_mail(mail)
    except EmailNotSent as exc:
        log.warning("Password reset email not sent: %s", exc)
    return {"queued": True}


@router.post("/reset", status_code=204)
async def reset(body: Reset, session: Session) -> None:
    row = await session.scalar(
        select(PasswordReset).where(PasswordReset.token_hash == sessions.token_hash(body.token))
    )
    if row is None or row.used_at is not None or row.expires_at <= sessions.now():
        raise ApiError(400, "reset_link_expired", EXPIRED_LINK)
    _new_password(body.password)
    user = await session.get(AppUser, row.user_id)
    assert user is not None
    row.used_at = sessions.now()
    user.password_hash = await passwords.hash_password(body.password)
    user.password_changed_at = sessions.now()
    user.failed_sign_ins = 0
    user.locked_until = None
    await sessions.end_all(session, user.id)
    await session.commit()


async def _check_password(session: AsyncSession, user: AppUser, password: str) -> None:
    _locked(user)
    if not await passwords.verify_password(password, user.password_hash):
        await _failed(session, user)
        raise ApiError(403, "wrong_password", "Your current password is not right")


@router.post("/password", status_code=204)
async def change_password(
    body: PasswordChange, user: CurrentUser, current: CurrentSession, session: Session
) -> None:
    """Change the password (S7). Every other browser is signed out."""
    await _check_password(session, user, body.current_password)
    _new_password(body.new_password)
    user.password_hash = await passwords.hash_password(body.new_password)
    user.password_changed_at = sessions.now()
    await sessions.end_all(session, user.id, keep=current)
    await session.commit()


@router.post("/two-step/start", response_model=TwoStepSetup)
async def two_step_start(user: CurrentUser, session: Session) -> TwoStepSetup:
    """A new secret to scan; two-step is on only after /two-step/confirm."""
    if user.totp_secret is not None:
        raise ApiError(409, "two_step_on", "Two-step sign-in is already on")
    secret = totp.new_secret()
    user.totp_pending_secret = secret
    await session.commit()
    uri = totp.uri(secret, user.email)
    return TwoStepSetup(secret=secret, uri=uri, qr_svg=totp.qr_svg(uri))


@router.post("/two-step/confirm", response_model=RecoveryCodes)
async def two_step_confirm(body: Code, user: CurrentUser, session: Session) -> RecoveryCodes:
    if user.totp_secret is not None:
        raise ApiError(409, "two_step_on", "Two-step sign-in is already on")
    if user.totp_pending_secret is None:
        raise ApiError(409, "no_setup", "Start setting up two-step sign-in first")
    step = totp.matching_step(user.totp_pending_secret, body.code, None)
    if step is None:
        raise ApiError(422, "wrong_code", "That code didn't work. Check the time on your phone.")
    codes = totp.new_recovery_codes()
    user.totp_secret = user.totp_pending_secret
    user.totp_pending_secret = None
    user.totp_last_step = step
    user.recovery_codes = [totp.recovery_hash(c) for c in codes]
    await session.commit()
    return RecoveryCodes(recovery_codes=codes)


@router.post("/two-step/off", status_code=204)
async def two_step_off(body: Password, user: CurrentUser, session: Session) -> None:
    await _check_password(session, user, body.password)
    user.totp_secret = None
    user.totp_pending_secret = None
    user.totp_last_step = None
    user.recovery_codes = []
    await session.commit()


@router.post("/two-step/recovery-codes", response_model=RecoveryCodes)
async def new_recovery_codes(body: Password, user: CurrentUser, session: Session) -> RecoveryCodes:
    """New recovery codes; the old ones stop working."""
    await _check_password(session, user, body.password)
    if user.totp_secret is None:
        raise ApiError(409, "two_step_off", "Two-step sign-in is off")
    codes = totp.new_recovery_codes()
    user.recovery_codes = [totp.recovery_hash(c) for c in codes]
    await session.commit()
    return RecoveryCodes(recovery_codes=codes)
