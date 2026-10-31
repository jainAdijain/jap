"""A5: the security code Greenhouse emails, read from JAP's mailbox over IMAP."""

import imaplib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

import pytest

from config import get_settings
from pipeline.apply import security_code as sc

PRESSED = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)
BODY = (
    "Hi Asha, copy and paste this code into the security code field on your application: "
    "Ab3dE5fG. This code expires in 10 minutes."
)


def _mail(sender: str, at: datetime, body: str, subject: str = "Security code") -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "jap-admin@example.com"
    msg["Subject"] = subject
    msg["Date"] = format_datetime(at)
    msg.set_content(body)
    return msg.as_bytes()


class FakeImap:
    mails: list[bytes] = []
    logins: list[tuple[str, str]] = []

    def __init__(self, host: str, port: int, timeout: int = 0) -> None:
        self.host = host

    def login(self, user: str, password: str) -> None:
        FakeImap.logins.append((user, password))

    def select(self, box: str, readonly: bool = False) -> None:
        pass

    def search(self, charset: Any, *criteria: str) -> tuple[str, list[bytes]]:
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(FakeImap.mails)))]

    def fetch(self, num: bytes, what: str) -> tuple[str, list[Any]]:
        return "OK", [(b"1 (RFC822)", FakeImap.mails[int(num) - 1]), b")"]

    def logout(self) -> None:
        pass


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeImap]]:
    monkeypatch.setenv("SMTP_USERNAME", "jap-admin@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    get_settings.cache_clear()
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    FakeImap.mails, FakeImap.logins = [], []
    yield FakeImap
    get_settings.cache_clear()


def test_the_code_is_found_in_the_email_text() -> None:
    assert sc.code_in(BODY, 8) == "Ab3dE5fG"
    assert sc.code_in("Your security code is 4F7K2Q9X", 8) == "4F7K2Q9X"
    assert sc.code_in("Your security code is: QWERTYUI", 8) == "QWERTYUI"
    # Plain words after "code" are not codes.
    assert sc.code_in("The security code expires tomorrow for everyone", 8) is None
    assert sc.code_in("No code here: 12345", 8) is None


def test_only_greenhouse_emails_after_submit_count(mailbox: type[FakeImap]) -> None:
    mailbox.mails = [
        _mail("no-reply@us.greenhouse-mail.io", PRESSED - timedelta(minutes=10),
              BODY.replace("Ab3dE5fG", "0ld0ld00")),
        _mail("Someone <someone@example.com>", PRESSED + timedelta(seconds=20),
              BODY.replace("Ab3dE5fG", "N0tTh1s0")),
        _mail("Greenhouse <no-reply@us.greenhouse-mail.io>", PRESSED + timedelta(seconds=30),
              BODY),
    ]  # fmt: skip
    assert sc.fetch_code("greenhouse", PRESSED, 8) == "Ab3dE5fG"
    assert mailbox.logins == [("jap-admin@example.com", "secret")]


def test_an_old_code_is_never_used(mailbox: type[FakeImap]) -> None:
    mailbox.mails = [_mail("no-reply@greenhouse.io", PRESSED - timedelta(minutes=5), BODY)]
    assert sc.fetch_code("greenhouse", PRESSED, 8) is None


async def test_the_waiter_polls_until_the_code_arrives(
    mailbox: type[FakeImap], monkeypatch: pytest.MonkeyPatch
) -> None:
    looks: list[int] = []
    real = sc.fetch_code

    def fetch(site: str, since: datetime, length: int) -> str | None:
        looks.append(1)
        if len(looks) == 2:
            mailbox.mails.append(
                _mail("no-reply@greenhouse.io", PRESSED + timedelta(seconds=5), BODY)
            )
        return real(site, since, length)

    monkeypatch.setattr(sc, "fetch_code", fetch)
    code = await sc.waiter("greenhouse", wait_s=1, poll_s=0)(PRESSED, 8)
    assert code == "Ab3dE5fG" and len(looks) == 2


async def test_the_waiter_gives_up(mailbox: type[FakeImap]) -> None:
    assert await sc.waiter("greenhouse", wait_s=0, poll_s=1)(PRESSED, 8) is None


def test_no_mailbox_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SMTP_USERNAME", "SMTP_PASSWORD", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    with pytest.raises(sc.MailboxError):
        sc.fetch_code("greenhouse", PRESSED, 8)
    get_settings.cache_clear()


def test_only_greenhouse_forms_read_mail() -> None:
    assert sc.site_of("https://job-boards.greenhouse.io/anthropic/jobs/1") == "greenhouse"
    assert sc.site_of("https://jobs.ashbyhq.com/linear/1/application") is None
