"""Cover letters (C1 to C6): written only when the form's cover letter field is mandatory.

The writer works from the frozen JD, the active resume and my tone note, and returns the
letter with the claims it makes. A second call lists every claim the letter makes about me
and quotes the resume for each; code then keeps a claim as found only when its quote really
is in the resume, and checks the length, the company and role, and that every number is in
the resume. Any problem regenerates the letter once with the problems listed; a second
failure is LetterFailed, which sends the attempt to Needs input (C6).

The model only ever sees redacted text and signs off as [NAME]; my name goes in locally.
The letter is also kept as a PDF built from a simple template (C4): it is uploaded to a
file field and attached to the confirmation email.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from llm import PersonalDataBlocked, SpendCapReached, call
from llm.client import ModelCallError, OutOfCredits
from llm.redact import PLACEHOLDER_RE, Sensitive, redact, restore

MAX_WORDS = 250
MAX_JD_CHARS = 12_000
MAX_RESUME_CHARS = 12_000
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


class WriterClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    resume_evidence: str


class LetterOut(BaseModel):
    letter: str
    claims: list[WriterClaim]


class CheckedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    found: bool
    evidence: str


class ClaimsOut(BaseModel):
    claims: list[CheckedClaim]


class LetterFailed(Exception):
    """Two letters in a row failed the checks, or the model could not be reached."""


@dataclass
class Letter:
    text: str  # my name filled in
    claims: list[dict[str, Any]]  # [{claim, found, evidence}], as stored in claim_check
    attempts: int
    prompt_excerpt: str  # the start of the redacted prompt, for the Inngest step output
    placeholders: list[str] = field(default_factory=list)  # what stood in for my details


def words(text: str) -> int:
    return len(text.split())


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w%+.#]", " ", text.lower()).split())


def problems(
    letter: str, claims: list[CheckedClaim], *, company: str, role: str, resume: str
) -> list[str]:
    """What is wrong with a letter (redacted text, before my name goes in)."""
    out: list[str] = []
    n = words(letter)
    if n >= MAX_WORDS:
        out.append(f"it has {n} words; stay under {MAX_WORDS}")
    body = _norm(letter)
    if company and _norm(company) not in body:
        out.append(f"name the company ({company})")
    if role and _norm(role) not in body:
        out.append(f"name the role ({role})")
    in_resume = _norm(resume)
    for c in claims:
        quote = _norm(c.evidence)
        if not c.found or not quote or quote not in in_resume:
            out.append(f'"{c.claim}" is not in the resume')
    allowed = set(NUMBER_RE.findall(resume)) | set(NUMBER_RE.findall(f"{company} {role}"))
    for num in dict.fromkeys(NUMBER_RE.findall(letter)):
        if num not in allowed:
            out.append(f"the number {num} is not in the resume")
    return out


async def generate(
    session: AsyncSession,
    *,
    company: str,
    title: str,
    role: str,
    jd_text: str,
    resume_text: str,
    tone_note: str | None,
    sensitive: Sensitive,
) -> Letter:
    """Write, check, and regenerate once on a problem (C3). LetterFailed after two."""
    resume = resume_text[:MAX_RESUME_CHARS]
    # What the model sees of the resume, so its quotes can be checked against it.
    seen = redact(resume, sensitive, generic=True).text
    fix = "none"
    issues: list[str] = []
    for attempt in (1, 2):
        try:
            written = await call(
                session,
                "cover_letter",
                {
                    "company": company,
                    "title": title,
                    "tone_note": tone_note or "none",
                    "fix": fix,
                    "frozen_jd_text": jd_text[:MAX_JD_CHARS],
                    "resume_text": resume,
                },
                LetterOut,
                sensitive=sensitive,
                slot="write",
                temperature=0.4,
                generic_redact=frozenset({"resume_text", "frozen_jd_text"}),
            )
            letter = written.output.letter.strip()
            checked = await call(
                session,
                "claim_check",
                {"letter": letter, "resume_text": resume},
                ClaimsOut,
                sensitive=sensitive,
                generic_redact=frozenset({"resume_text", "letter"}),
            )
        except (SpendCapReached, PersonalDataBlocked, OutOfCredits) as exc:
            raise LetterFailed(str(exc)) from exc
        except ModelCallError as exc:
            issues = [f"the model failed: {exc}"]
            continue
        claims = checked.output.claims
        issues = problems(letter, claims, company=company, role=role, resume=seen)
        if not issues:
            return Letter(
                text=restore(letter, sensitive, written.links).strip(),
                claims=[c.model_dump() for c in claims],
                attempts=attempt,
                prompt_excerpt=written.model_input[:800],
                placeholders=sorted(set(PLACEHOLDER_RE.findall(written.model_input))),
            )
        fix = "; ".join(issues)
    raise LetterFailed("; ".join(issues))


PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 22mm 22mm; }}
body {{ font-family: Georgia, 'DejaVu Serif', serif; font-size: 11pt; line-height: 1.5;
  color: #111; }}
.who {{ font-size: 15pt; font-weight: bold; margin: 0; }}
.contact, .date {{ color: #444; margin: 2px 0 0; }}
.date {{ margin-top: 18px; }}
.re {{ margin: 18px 0 14px; font-weight: bold; }}
p {{ margin: 0 0 10px; }}
</style></head><body>
<p class="who">{name}</p><p class="contact">{contact}</p>
<p class="date">{date}</p>
<p class="re">{title}, {company}</p>
{body}
</body></html>"""


async def write_pdf(
    path: Path, text: str, *, name: str, contact: str, company: str, title: str
) -> Path:
    """The letter as a one-page PDF from a plain template (C4), built in local Chromium."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    body = "\n".join("<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in paragraphs)
    page_html = PAGE.format(
        name=html.escape(name),
        contact=html.escape(contact),
        date=datetime.now(UTC).strftime("%d %B %Y"),
        title=html.escape(title),
        company=html.escape(company),
        body=body,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(page_html)
            await page.pdf(path=str(path), format="A4", print_background=False)
        finally:
            await browser.close()
    return path


def upload_name(full_name: str | None, company: str) -> str:
    """The file name the employer sees: Asha-Rao-Cover-Letter-Acme.pdf."""
    parts = [full_name or "", "Cover Letter", company]
    slug = "-".join(re.sub(r"[^\w]+", "-", p).strip("-") for p in parts if p.strip())
    return f"{slug}.pdf"
