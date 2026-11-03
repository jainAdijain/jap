"""Two-step sign-in codes (PRD S3): RFC 6238 TOTP, 6 digits, 30 seconds, SHA-1.

That is what Google Authenticator, 1Password, Authy and the rest expect. A code is accepted
for the step before and after now (clock drift), and never twice: the step it matched is
stored and only later steps count.
"""

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

import segno

STEP = 30
DIGITS = 6
ISSUER = "JAP"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def now_step(at: float | None = None) -> int:
    return int((time.time() if at is None else at) // STEP)


def matching_step(secret: str, code: str, after: int | None, at: float | None = None) -> int | None:
    """The step `code` belongs to (now, or one either side), or None. Steps <= after never match."""
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = now_step(at)
    for step in (current - 1, current, current + 1):
        if after is not None and step <= after:
            continue
        if hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def uri(secret: str, email: str) -> str:
    label = quote(f"{ISSUER}:{email}", safe=":@")
    return f"otpauth://totp/{label}?secret={secret}&issuer={ISSUER}&digits={DIGITS}&period={STEP}"


def qr_svg(text: str) -> str:
    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="svg", scale=5, border=2, xmldecl=False)
    return out.getvalue().decode()


# Recovery codes: 8 single-use codes shown once; only their SHA-256 is kept.
RECOVERY_COUNT = 8
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_recovery_codes() -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"

    return [one() for _ in range(RECOVERY_COUNT)]


def recovery_hash(code: str) -> str:
    cleaned = "".join(code.lower().split()).replace("-", "")
    return hashlib.sha256(cleaned.encode()).hexdigest()
