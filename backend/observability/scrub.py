"""Redaction for log lines and Sentry errors (PRD O2, O15).

Log lines never hold resume text, answers, passwords, session tokens, cookies or emailed
codes. The code never logs those on purpose; this is the safety net for what slips through
a message or an exception: email addresses, phone numbers, keys and tokens, values after a
secret-sounding name (password=..., "token": "..."), credentials inside URLs, and the
security code a job site emails. Field names that hold personal content are dropped whole.
"""

import re
from typing import Any

from llm.redact import EMAIL_RE

SECRET = "[SECRET]"
REDACTED = "[REDACTED]"

# Field names whose value is never logged, whatever it holds.
SECRET_KEYS = re.compile(
    r"pass(word|wd)?|pwd|secret|token|api[_-]?key|authorization|auth|cookie|session|"
    r"otp|security[_-]?code|^code$|dsn|answers?|resume(_text)?|cover[_-]?letter(_text)?|"
    r"letter|body|description(_text|_html)?|jd(_snapshot)?|form_fields|profile",
    re.IGNORECASE,
)

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
URL_CREDS_RE = re.compile(r"(?<=://)[^/\s:@]+(?::[^/\s@]*)?@")
BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
KEY_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}|\bghp_[A-Za-z0-9]{20,}")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")
NAMED_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|session(?:_id)?|"
    r"otp|code|dsn)(\"?\s*[:=]\s*\"?)([^\s,;\"'&}]+)"
)
CODE_RE = re.compile(r"(?i)(\bcode\b[^0-9\n]{0,24})\b\d{4,8}\b")
INTL_PHONE_RE = re.compile(r"(?<![\w+-])\+\d[\d\s().-]{7,16}\d(?![\w-])")
LOCAL_PHONE_RE = re.compile(r"(?<![\w-])(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\w-])")
MOBILE_RE = re.compile(r"(?<![\w.-])\d{10}(?![\w.-])")


def scrub(text: str) -> str:
    """The text with secrets, codes, emails and phone numbers replaced."""
    if not text:
        return text
    # UUIDs look like long digit runs in places; keep them out of the phone patterns.
    kept: list[str] = []

    def keep(m: re.Match[str]) -> str:
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"

    out = UUID_RE.sub(keep, text)
    out = URL_CREDS_RE.sub(f"{SECRET}@", out)
    out = BEARER_RE.sub(lambda m: f"{m.group(1)} {SECRET}", out)
    out = KEY_RE.sub(SECRET, out)
    out = JWT_RE.sub(SECRET, out)
    out = NAMED_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{SECRET}", out)
    out = CODE_RE.sub(lambda m: f"{m.group(1)}{SECRET}", out)
    out = EMAIL_RE.sub("[EMAIL]", out)
    out = INTL_PHONE_RE.sub("[PHONE]", out)
    out = LOCAL_PHONE_RE.sub("[PHONE]", out)
    out = MOBILE_RE.sub("[PHONE]", out)
    return re.sub("\x00(\\d+)\x00", lambda m: kept[int(m.group(1))], out)


def scrub_value(value: Any, depth: int = 0) -> Any:
    """A JSON-like value with every string scrubbed and secret-named keys dropped."""
    if depth > 6:
        return REDACTED
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {
            str(k): REDACTED if SECRET_KEYS.search(str(k)) else scrub_value(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [scrub_value(v, depth + 1) for v in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return scrub(str(value))
