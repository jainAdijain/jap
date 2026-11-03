"""Account fixes from the machine JAP runs on, for when the browser can't (PRD S3).

python -m auth.cli two-step-off <email>   turn two-step sign-in off (phone and codes lost)
python -m auth.cli sign-out-all <email>   end every session of that account
python -m auth.cli set-password <email>   set a new password (asks for it twice)
"""

import asyncio
import getpass
import sys

from sqlalchemy import func, select

from auth import passwords, sessions
from database.models import AppUser
from database.session import get_sessionmaker

COMMANDS = ("two-step-off", "sign-out-all", "set-password")


async def _run(command: str, email: str) -> str:
    async with get_sessionmaker()() as session:
        user = await session.scalar(
            select(AppUser).where(func.lower(AppUser.email) == email.lower())
        )
        if user is None:
            return f"No JAP account has the address {email}."
        if command == "two-step-off":
            user.totp_secret = None
            user.totp_pending_secret = None
            user.totp_last_step = None
            user.recovery_codes = []
            await session.commit()
            return f"Two-step sign-in is off for {user.email}."
        if command == "sign-out-all":
            await sessions.end_all(session, user.id)
            await session.commit()
            return f"Every session for {user.email} has ended."
        new = getpass.getpass("New password: ")
        if new != getpass.getpass("Same password again: "):
            return "The two passwords are different. Nothing changed."
        problem = passwords.problem(new)
        if problem:
            return f"{problem}. Nothing changed."
        user.password_hash = await passwords.hash_password(new)
        user.password_changed_at = sessions.now()
        user.failed_sign_ins = 0
        user.locked_until = None
        await sessions.end_all(session, user.id)
        await session.commit()
        return f"New password set for {user.email}. Every session has ended."


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in COMMANDS:
        raise SystemExit(__doc__)
    print(asyncio.run(_run(sys.argv[1], sys.argv[2])))
