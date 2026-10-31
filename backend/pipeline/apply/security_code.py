"""Read a security code the job site emailed (A5).

Greenhouse sometimes asks for an 8-character code it emails before it takes an application.
A Gmail filter forwards those emails to JAP's own mailbox (E4), and this module reads them
over IMAP with the same login: only emails from the job site that arrived after Submit was
pressed, polled for up to 3 minutes. The code is returned to the browser and never stored,
logged or sent to a model.

`python -m pipeline.apply.security_code` (make code-mail-check) logs in once and says how
many emails from Greenhouse the inbox holds, to check the forwarding.
"""

import asyncio
import email
import imaplib
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.utils import parsedate_to_datetime

from config import get_settings

WAIT_S = 180
POLL_S = 10
# The mail server's clock and ours may differ a little.
SKEW = timedelta(seconds=60)
SENDERS = {"greenhouse": re.compile(r"greenhouse", re.I)}

GetCode = Callable[[datetime, int], Awaitable[str | None]]


class MailboxError(Exception):
    """The mailbox could not be reached or logged in to."""


def _text(msg: Message) -> str:
    parts: list[str] = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if part.get_content_type() == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
        parts.append(text)
    return " ".join(" ".join(parts).split())


def code_in(text: str, length: int) -> str | None:
    """The code in an email's text: the first token of exactly `length` letters and digits
    after the word "code" with a digit or mixed case, else the one all-capitals token."""
    at = re.search(r"\bcode\b", text, re.I)
    if at is None:
        return None
    tokens = re.findall(rf"\b[A-Za-z0-9]{{{length}}}\b", text[at.end() :])
    for token in tokens:
        if any(c.isdigit() for c in token) or (token != token.lower() and token != token.upper()):
            return str(token)
    # All capitals with no digit: taken only when it is the one such token in the email.
    caps = [t for t in tokens if t.isupper()]
    return str(caps[0]) if len(caps) == 1 else None


def _from_site(msg: Message, site: str) -> bool:
    pattern = SENDERS.get(site, re.compile(re.escape(site), re.I))
    # A Gmail filter forward keeps the original sender; the subject and body also name it.
    head = " ".join(str(msg.get(h, "")) for h in ("From", "Reply-To", "Subject", "Sender"))
    return bool(pattern.search(head))


def _arrived(msg: Message) -> datetime | None:
    try:
        when = parsedate_to_datetime(str(msg.get("Date", "")))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _connect() -> imaplib.IMAP4:
    s = get_settings()
    if not s.email_from or not s.email_password:
        raise MailboxError("No mailbox in .env (SMTP_USERNAME and SMTP_PASSWORD)")
    try:
        box: imaplib.IMAP4 = (
            imaplib.IMAP4_SSL(s.imap_host, s.imap_port, timeout=20)
            if s.imap_ssl
            else imaplib.IMAP4(s.imap_host, s.imap_port, timeout=20)
        )
        box.login(s.email_from, s.email_password)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailboxError(f"Could not log in to {s.imap_host}: {exc}") from exc
    return box


def fetch_code(site: str, since: datetime, length: int) -> str | None:
    """One look at the inbox: the code in the newest email from the site that arrived
    after `since`, or None."""
    box = _connect()
    try:
        box.select("INBOX", readonly=True)
        day = (since - SKEW).strftime("%d-%b-%Y")
        _typ, data = box.search(None, "SINCE", day)
        ids = (data[0] or b"").split() if data else []
        for num in reversed(ids[-50:]):  # newest first
            _typ, parts = box.fetch(num.decode(), "(RFC822)")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
            if not isinstance(raw, bytes):
                continue
            msg = email.message_from_bytes(raw)
            when = _arrived(msg)
            if when is None or when < since - SKEW or not _from_site(msg, site):
                continue
            code = code_in(_text(msg), length)
            if code:
                return code
        return None
    finally:
        try:
            box.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


def waiter(site: str, wait_s: int = WAIT_S, poll_s: int = POLL_S) -> GetCode:
    """A get_code for submit_form: polls the inbox until a code arrives or wait_s passes."""

    async def get_code(since: datetime, length: int) -> str | None:
        deadline = asyncio.get_running_loop().time() + wait_s
        while True:
            code = await asyncio.to_thread(fetch_code, site, since, length)
            if code:
                return code
            if asyncio.get_running_loop().time() + poll_s > deadline:
                return None
            await asyncio.sleep(poll_s)

    return get_code


def site_of(url: str) -> str | None:
    """The job site whose code emails JAP can read, from the form's URL."""
    return "greenhouse" if "greenhouse.io" in url.lower() else None


def check() -> int:
    s = get_settings()
    box = _connect()
    try:
        box.select("INBOX", readonly=True)
        since = (datetime.now(UTC) - timedelta(days=30)).strftime("%d-%b-%Y")
        _typ, data = box.search(None, "SINCE", since, "FROM", "greenhouse")
        count = len((data[0] or b"").split()) if data else 0
    finally:
        box.logout()
    print(f"Logged in to {s.imap_host} as {s.email_from}.")
    print(f"Emails from Greenhouse in the last 30 days: {count}.")
    if count == 0:
        print("None yet. Check the Gmail filter forwards Greenhouse security code emails here.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(check())
    except MailboxError as exc:
        print(exc)
        sys.exit(1)
