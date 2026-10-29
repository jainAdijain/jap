"""Contact fields filled by code (PII masking): a model never sees or fills them.

A field is matched to name, first name, last name, email, phone, LinkedIn, GitHub, website
or location by its label and input type. The resume file goes into a file field labelled
resume or CV. Questions only I can answer (notice period, salary, start date, work
authorisation, relocation) are spotted here too, so no model is asked to invent them.
"""

import re
from collections.abc import Callable

from database.models import Profile
from pipeline.apply.form import FormField


def _norm(label: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", label.lower()).split())


def _link(profile: Profile, *hosts: str) -> str | None:
    for link in profile.links or []:
        url = str(link.get("url") or "")
        label = str(link.get("label") or "").lower()
        if any(h in url.lower() or h in label for h in hosts):
            return url
    return None


def _other_link(profile: Profile) -> str | None:
    for link in profile.links or []:
        url = str(link.get("url") or "")
        if url and not re.search(r"linkedin|github", url, re.I):
            return url
    return None


def _name_part(profile: Profile, first: bool) -> str | None:
    parts = (profile.full_name or "").split()
    if not parts:
        return None
    if first:
        return parts[0]
    return " ".join(parts[1:]) or None


# (source, label pattern, value). Order matters: first and last name before name.
RULES: list[tuple[str, re.Pattern[str], Callable[[Profile], str | None]]] = [
    ("first_name", re.compile(r"\b(first|given|fore) ?name\b"), lambda p: _name_part(p, True)),
    ("last_name", re.compile(r"\b(last|family|sur) ?name\b|\bsurname\b"),
     lambda p: _name_part(p, False)),
    ("email", re.compile(r"\be ?mail\b"), lambda p: p.email),
    ("phone", re.compile(r"\b(phone|mobile|telephone|cell)\b"), lambda p: p.phone),
    ("linkedin", re.compile(r"\blinked ?in\b"), lambda p: _link(p, "linkedin")),
    ("github", re.compile(r"\bgit ?hub\b"), lambda p: _link(p, "github")),
    ("website", re.compile(r"\b(website|portfolio|personal (site|url)|blog)\b"), _other_link),
    ("location", re.compile(r"^(current )?(location|city|where are you (based|located))\b"),
     lambda p: p.locations[0] if p.locations else None),
    ("name", re.compile(r"^(full |legal |your )?name$"), lambda p: p.full_name),
]  # fmt: skip
TYPE_RULES = {"email": "email", "tel": "phone"}

RESUME = re.compile(r"\b(resume|résumé|cv|curriculum)\b", re.I)
COVER_LETTER = re.compile(r"\bcover ?letter\b", re.I)

# A fact only I know (Apply flow step 5): never drafted, always asked.
MY_FACT = re.compile(
    r"notice period|salary|compensation|pay expectation|ctc\b|start date|when can you start"
    r"|earliest|available to start|authori[sz]ed to work|work authori[sz]ation|visa|sponsor"
    r"|relocat|right to work|citizenship|gender|race|ethnic|veteran|disabilit|pronoun"
    r"|date of birth|\bage\b|criminal|background check",
    re.I,
)


def contact_value(f: FormField, profile: Profile) -> tuple[str, str] | None:
    """(source, value) for a contact field, or None when it is not one (or the profile
    has no value for it)."""
    if f.type in ("file", "select", "radio", "checkbox", "checkboxes", "textarea"):
        return None
    label = _norm(f.label)
    for source, pattern, value in RULES:
        if pattern.search(label):
            v = value(profile)
            return (source, v) if v else None
    by_type = TYPE_RULES.get(f.type)
    if by_type:
        v = profile.email if by_type == "email" else profile.phone
        return (by_type, v) if v else None
    return None


def is_resume_field(f: FormField) -> bool:
    return f.type == "file" and bool(RESUME.search(f.label) or RESUME.search(f.key))


def is_cover_letter_field(f: FormField) -> bool:
    return bool(COVER_LETTER.search(f.label) or COVER_LETTER.search(f.key))


def needs_my_fact(f: FormField) -> bool:
    return bool(MY_FACT.search(f.label))


def is_free_text(f: FormField) -> bool:
    return f.type in ("text", "textarea")
