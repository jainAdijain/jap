"""Contact fields filled by code (PII masking): a model never sees or fills them.

A field is matched to name, first name, last name, email, phone, LinkedIn, GitHub, website
or location by its label and input type. The resume file goes into a file field labelled
resume or CV. Questions only I can answer (notice period, salary, start date, work
authorisation, relocation) are spotted here too, so no model is asked to invent them, and
so is a cover letter field, which pipeline/cover_letter.py fills.

A phone field on a form with no country code picker of its own (Ashby) gets the number with
its country code, taken from where I live when the profile's number has none (JAP-11: the
Atlan form was sent with a bare 10-digit Indian number). A location typeahead (Ashby's
Location, read as a combobox with no options until something is typed) is filled from My
profile's first location like a text field.
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

# A country code picker beside the phone number (Greenhouse's "Country" with +91).
COUNTRY_PICKER = re.compile(r"^country$|\b(country|dial(l?ing)?|calling) code\b", re.I)
# Country calling codes, by the country (or a big city) named in my address or locations.
DIAL_CODES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(india|bengaluru|bangalore|mumbai|delhi|gurgaon|gurugram|noida|pune"
                r"|hyderabad|chennai|kolkata|ahmedabad)\b", re.I), "+91"),
    (re.compile(r"\b(united states|usa|u\.s\.a?\.?|new york|san francisco|seattle)\b", re.I), "+1"),
    (re.compile(r"\b(canada|toronto|vancouver)\b", re.I), "+1"),
    (re.compile(r"\b(united kingdom|uk|england|london)\b", re.I), "+44"),
    (re.compile(r"\b(germany|berlin|munich)\b", re.I), "+49"),
    (re.compile(r"\bsingapore\b", re.I), "+65"),
    (re.compile(r"\b(united arab emirates|uae|dubai)\b", re.I), "+971"),
    (re.compile(r"\b(australia|sydney|melbourne)\b", re.I), "+61"),
    (re.compile(r"\b(netherlands|amsterdam)\b", re.I), "+31"),
    (re.compile(r"\b(ireland|dublin)\b", re.I), "+353"),
]  # fmt: skip


def dial_code(profile: Profile) -> str | None:
    where = " ".join([profile.address or "", *(profile.locations or [])])
    return next((code for pattern, code in DIAL_CODES if pattern.search(where)), None)


def international(phone: str, profile: Profile) -> str:
    """The number with its country code: as saved when it has one, else with the code of
    the country I live in. A number JAP can't place is left as it is."""
    p = phone.strip()
    if p.startswith("+") or p.startswith("00"):
        return p
    code = dial_code(profile)
    digits = re.sub(r"\D", "", p)
    if code is None or len(digits) < 7:
        return p
    if digits.startswith(code[1:]) and len(digits) > 10:
        return f"+{p}"  # saved as 91 98765 43210
    return f"{code} {p.lstrip('0')}"


def has_country_picker(fields: list[FormField]) -> bool:
    return any(f.type == "select" and COUNTRY_PICKER.search(f.label.strip()) for f in fields)


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


def contact_value(
    f: FormField, profile: Profile, *, country_picker: bool = True
) -> tuple[str, str] | None:
    """(source, value) for a contact field, or None when it is not one (or the profile
    has no value for it). With no country picker on the form, a phone number gets its
    country code."""
    typeahead = f.widget == "combobox" and not f.options
    if f.type in ("file", "radio", "checkbox", "checkboxes", "textarea") or (
        f.type == "select" and not typeahead
    ):
        return None
    label = _norm(f.label)
    found: tuple[str, str | None] | None = None
    for source, pattern, value in RULES:
        if pattern.search(label):
            found = (source, value(profile))
            break
    if found is None and (by_type := TYPE_RULES.get(f.type)):
        found = (by_type, profile.email if by_type == "email" else profile.phone)
    if found is None or not found[1]:
        return None
    source, v = found[0], str(found[1])
    if typeahead and source != "location":
        return None  # a typeahead is a place; anything else picks from its list
    if source == "phone" and not country_picker:
        v = international(v, profile)
    return source, v


def is_resume_field(f: FormField) -> bool:
    return f.type == "file" and bool(RESUME.search(f.label) or RESUME.search(f.key))


def is_cover_letter_field(f: FormField) -> bool:
    """A field that takes the letter itself: a file, or text to paste (C4)."""
    named = bool(COVER_LETTER.search(f.label) or COVER_LETTER.search(f.key))
    return named and f.type in ("file", "text", "textarea")


def needs_my_fact(f: FormField) -> bool:
    return bool(MY_FACT.search(f.label))


def is_free_text(f: FormField) -> bool:
    return f.type in ("text", "textarea")
