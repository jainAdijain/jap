"""The SMTP sender (E4): GoDaddy Professional Email by default, old Gmail settings still read."""

import smtplib
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from config import get_settings
from pipeline import notify
from pipeline.notify import EmailNotSent, Mail

MAIL = Mail(to="me@example.com", subject="Applied: Engineer at Acme", body="You applied.\n")


class FakeSMTP:
    """Stands in for smtplib.SMTP_SSL and records what JAP did with it."""

    last: "FakeSMTP"

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        self.host, self.port = host, port
        self.login_as: tuple[str, str] | None = None
        self.sent: list[EmailMessage] = []
        FakeSMTP.last = self

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        self.login_as = (user, password)

    def send_message(self, msg: EmailMessage) -> None:
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def smtp(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "GMAIL_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_godaddy_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SMTP_USERNAME", "aditya@example.in")
    monkeypatch.setenv("SMTP_PASSWORD", "mailbox-pass")
    get_settings.cache_clear()
    pdf = tmp_path / "letter.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    await notify.send(
        Mail(to="me@example.com", subject="S", body="B", attachment=pdf, attachment_name="L.pdf")
    )
    smtp = FakeSMTP.last
    assert (smtp.host, smtp.port) == ("smtpout.secureserver.net", 465)
    assert smtp.login_as == ("aditya@example.in", "mailbox-pass")
    (msg,) = smtp.sent
    assert msg["From"] == "aditya@example.in" and msg["To"] == "me@example.com"
    (att,) = list(msg.iter_attachments())
    assert att.get_filename() == "L.pdf"


async def test_old_gmail_settings_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pass")
    get_settings.cache_clear()
    await notify.send(MAIL)
    assert FakeSMTP.last.host == "smtp.gmail.com"
    assert FakeSMTP.last.login_as == ("me@gmail.com", "app-pass")


async def test_no_mailbox_set() -> None:
    with pytest.raises(EmailNotSent, match="SMTP_USERNAME"):
        await notify.send(MAIL)
