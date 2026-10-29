"""Keeps personal details away from every model (PRD Privacy, design doc PII masking).

redact() swaps names, emails, phones, addresses and links for placeholders, guard()
blocks a request body that still holds any of them, and restore() puts real values back
into model output locally.
"""

import re
from dataclasses import dataclass, field

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>()\[\]]+|\b(?:linkedin\.com|github\.com|gitlab\.com)/[^\s<>()\[\]]+",
    re.IGNORECASE,
)
# 10 to 15 digits, allowing the separators people type in phone numbers.
PHONE_RE = re.compile(r"(?<![\w+])\+?\(?\d(?:[\s().-]?\d){9,14}(?!\w)")
PLACEHOLDER_RE = re.compile(r"\[(?:NAME|EMAIL|PHONE|ADDRESS|LINK(?:_\d+)?)\]")


class PersonalDataBlocked(Exception):
    """A model request still contained a personal detail; nothing was sent."""


@dataclass
class Sensitive:
    """The personal values to keep out of model input."""

    name: str | None = None
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    def merge(self, other: "Sensitive") -> "Sensitive":
        return Sensitive(
            name=self.name or other.name,
            emails=_unique(self.emails + other.emails),
            phones=_unique(self.phones + other.phones),
            addresses=_unique(self.addresses + other.addresses),
            links=_unique(self.links + other.links),
        )

    @property
    def name_parts(self) -> list[str]:
        if not self.name:
            return []
        parts = [p for p in re.split(r"[\s.,]+", self.name) if len(p) >= 3]
        return _unique([self.name.strip(), *parts])


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i.strip() for i in items if i and i.strip()))


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _strip_scheme(url: str) -> str:
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", url.strip(), flags=re.IGNORECASE).rstrip("/")


def _handle(url: str) -> str | None:
    """The account name in a profile link: github.com/jdoe -> jdoe."""
    path = _strip_scheme(url).split("/", 1)
    if len(path) < 2:
        return None
    segs = [s for s in path[1].split("/") if s and s.lower() not in {"in", "pub", "u", "user"}]
    return segs[0] if segs and len(segs[0]) >= 3 else None


def from_resume_text(text: str) -> Sensitive:
    """Values found locally in a resume, for before My profile has contact details."""
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    name = first_line if 0 < len(first_line) <= 60 and not EMAIL_RE.search(first_line) else None
    return Sensitive(
        name=name,
        emails=EMAIL_RE.findall(text),
        phones=[m.group(0) for m in PHONE_RE.finditer(text)],
        links=[m.group(0).rstrip(".,;") for m in URL_RE.finditer(text)],
    )


def find_links(text: str) -> list[str]:
    """Profile links in resume text (LinkedIn, GitHub, sites), found without a model."""
    return _unique([m.group(0).rstrip(".,;") for m in URL_RE.finditer(text)])


def _flex(value: str) -> str:
    """A regex for value that tolerates any case and spacing."""
    return r"\s+".join(re.escape(w) for w in value.split())


def _phone_pattern(digits: str) -> str:
    return r"\+?" + r"[\s().-]*".join(re.escape(d) for d in digits)


@dataclass
class Redaction:
    text: str
    links: dict[str, str]  # placeholder -> real url


def redact(text: str, values: Sensitive, *, generic: bool = False) -> Redaction:
    """Replace every personal value in text, longest first.

    generic=True also removes any email, phone-like number or URL (used on resume text).
    """
    subs: list[tuple[str, str]] = []  # (regex, placeholder)
    links: dict[str, str] = {}
    for i, url in enumerate(values.links, start=1):
        ph = f"[LINK_{i}]"
        links[ph] = url
        bare = _strip_scheme(url)
        subs.append((r"(?:https?://)?(?:www\.)?" + re.escape(bare) + r"/?", ph))
        handle = _handle(url)
        if handle:
            subs.append((r"(?<![\w@])" + re.escape(handle) + r"(?!\w)", ph))
    for email in values.emails:
        subs.append((re.escape(email), "[EMAIL]"))
    for phone in values.phones:
        digits = _digits(phone)
        if len(digits) >= 7:
            subs.append((_phone_pattern(digits), "[PHONE]"))
            if len(digits) > 10:  # also the local number without the country code
                subs.append((r"(?<!\d)" + _phone_pattern(digits[-10:]), "[PHONE]"))
    for address in values.addresses:
        lines = [ln.strip(" ,") for ln in address.splitlines() if len(ln.strip(" ,")) >= 4]
        for part in [address, *lines]:
            subs.append((_flex(part.strip()), "[ADDRESS]"))
    for part in values.name_parts:
        subs.append((r"(?<!\w)" + _flex(part) + r"(?!\w)", "[NAME]"))

    # Longest literal first, so a full name goes before its parts.
    subs.sort(key=lambda s: len(s[0]), reverse=True)
    out = text
    for pattern, ph in subs:
        out = re.sub(pattern, ph, out, flags=re.IGNORECASE)
    if generic:
        out = EMAIL_RE.sub("[EMAIL]", out)
        out = URL_RE.sub("[LINK]", out)
        out = PHONE_RE.sub("[PHONE]", out)
    return Redaction(text=out, links=links)


def guard(body: str, values: Sensitive) -> None:
    """Raise PersonalDataBlocked if body still contains any personal value."""
    norm = _norm(body)
    digits = _digits(body)
    hits: list[str] = []
    for part in values.name_parts:
        if re.search(r"(?<!\w)" + re.escape(_norm(part)) + r"(?!\w)", norm):
            hits.append("name")
    for email in values.emails:
        if _norm(email) in norm:
            hits.append("email")
    for phone in values.phones:
        d = _digits(phone)
        if len(d) >= 7 and (d in digits or (len(d) > 10 and d[-10:] in digits)):
            hits.append("phone")
    for address in values.addresses:
        lines = [ln.strip(" ,") for ln in address.splitlines() if len(ln.strip(" ,")) >= 4]
        if any(_norm(part) in norm for part in [address, *lines]):
            hits.append("address")
    for url in values.links:
        handle = _handle(url)
        if _norm(_strip_scheme(url)) in norm or (
            handle and re.search(r"(?<![\w@])" + re.escape(handle.lower()) + r"(?!\w)", norm)
        ):
            hits.append("link")
    if hits:
        raise PersonalDataBlocked(
            f"personal detail in model input ({', '.join(sorted(set(hits)))})"
        )


def restore(text: str, values: Sensitive, links: dict[str, str]) -> str:
    """Put real values back into model output; drop placeholders with no value."""

    def sub(m: re.Match[str]) -> str:
        ph = m.group(0)
        if ph == "[NAME]":
            return values.name or ""
        if ph == "[EMAIL]":
            return values.emails[0] if values.emails else ""
        if ph == "[PHONE]":
            return values.phones[0] if values.phones else ""
        if ph == "[ADDRESS]":
            return values.addresses[0] if values.addresses else ""
        return links.get(ph, "")

    return PLACEHOLDER_RE.sub(sub, text)


def strip_placeholders(text: str) -> str:
    return " ".join(PLACEHOLDER_RE.sub("", text).split())
