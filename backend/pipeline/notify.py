"""The confirmation email (E1 to E3): "Applied: Role at Company" to the address on My profile,
sent once per Applied application by notify-applied. Gmail SMTP with an app password.
"""

import asyncio
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import Application, Company, Listing, Profile

PLATFORMS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}


class EmailNotSent(Exception):
    pass


@dataclass
class Mail:
    to: str
    subject: str
    body: str
    attachment: Path | None = None


async def build(session: AsyncSession, app_id: uuid.UUID) -> Mail | None:
    """The email for an Applied application, or None when it was already sent."""
    settings = get_settings()
    app = await session.get(Application, app_id)
    if app is None or app.status != "applied":
        raise EmailNotSent(f"application {app_id} is not Applied")
    if app.email_sent_at is not None:
        return None
    listing = await session.get(Listing, app.listing_id)
    profile = await session.scalar(select(Profile))
    assert listing is not None
    company = await session.get(Company, listing.company_id)
    if not profile or not profile.email:
        raise EmailNotSent("My profile has no email address")
    jd = app.jd_snapshot or {}
    title = str(jd.get("title") or listing.title_display)
    company_name = company.name if company else ""
    at = (app.submitted_at or datetime.now(UTC)).astimezone(ZoneInfo(settings.jap_tz))
    panel = f"{settings.jap_ui_url.rstrip('/')}/?listing={listing.id}"
    lines = [
        f"You applied for {title} at {company_name}.",
        "",
        f"Role: {title} ({listing.canonical_role})",
        f"Company: {company_name}",
        f"Locations: {', '.join(jd.get('locations') or listing.locations) or 'not stated'}",
        f"Work mode: {listing.work_mode or 'not stated'}",
        f"Applied through: {PLATFORMS.get(str(jd.get('source')), str(jd.get('source') or ''))}",
        f"Applied at: {at:%d %b %Y, %H:%M} ({settings.jap_tz})",
        "Status: Applied",
        "",
        f"Original posting: {jd.get('posting_url') or ''}",
        f"Frozen JD in JAP: {panel}",
    ]
    letter = Path(app.cover_letter_path) if app.cover_letter_path else None
    return Mail(
        to=profile.email,
        subject=f"Applied: {title} at {company_name}",
        body="\n".join(lines) + "\n",
        attachment=letter if letter and letter.exists() else None,
    )


def _send_sync(mail: Mail) -> None:
    settings = get_settings()
    if not settings.gmail_address:
        raise EmailNotSent("GMAIL_ADDRESS is not set in .env")
    msg = EmailMessage()
    msg["From"] = settings.gmail_address
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg.set_content(mail.body)
    if mail.attachment is not None:
        msg.add_attachment(
            mail.attachment.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=mail.attachment.name,
        )
    smtp: smtplib.SMTP
    if settings.smtp_ssl:
        smtp = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, context=ssl.create_default_context(), timeout=30
        )
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    with smtp:
        if settings.gmail_app_password:
            smtp.login(settings.gmail_address, settings.gmail_app_password)
        smtp.send_message(msg)


async def send(mail: Mail) -> None:
    try:
        await asyncio.to_thread(_send_sync, mail)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailNotSent(f"SMTP failed: {exc}") from exc


async def mark_sent(session: AsyncSession, app_id: uuid.UUID) -> None:
    app = await session.get(Application, app_id)
    if app is not None and app.email_sent_at is None:
        app.email_sent_at = datetime.now(UTC)
        await session.commit()
