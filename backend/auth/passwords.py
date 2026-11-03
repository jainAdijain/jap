"""Password hashing with scrypt from the standard library (PRD S2).

A stored hash reads scrypt$<n>$<r>$<p>$<salt>$<hash> (base64), so the cost can be raised later
without breaking old hashes. Hashing takes about a tenth of a second, so it runs in a thread.
"""

import asyncio
import base64
import hashlib
import hmac
import secrets

N, R, P = 2**15, 8, 1
MAXMEM = 64 * 1024 * 1024
MIN_LENGTH = 12
MAX_LENGTH = 256


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _hash_sync(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, maxmem=MAXMEM)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def _verify_sync(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=MAXMEM,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash_sync, password)


async def verify_password(password: str, stored: str) -> bool:
    return await asyncio.to_thread(_verify_sync, password, stored)


# Checked against when the email is unknown, so a wrong email takes as long as a wrong password.
DUMMY_HASH = _hash_sync(secrets.token_urlsafe(16))


def problem(password: str) -> str | None:
    """Why a new password is refused, or None."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters"
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters"
    return None
