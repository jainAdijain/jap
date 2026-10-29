"""Canonical company and role (PRD Cross-platform dedup).

A company name is lowercased, stripped of legal suffixes and resolved through company_alias.
A title is lowercased, abbreviations are expanded, and locations, work mode words,
requisition numbers and brackets are removed. Seniority and specialization stay, so Senior
and Junior, or Backend and Frontend, are different roles.
"""

import re

LEGAL = {
    "inc", "incorporated", "ltd", "limited", "llc", "llp", "pvt", "private", "plc", "gmbh",
    "corp", "corporation", "co", "company", "technologies", "technology", "tech", "labs", "lab",
    "holdings", "group", "sa", "ag", "bv", "oy", "ab",
}  # fmt: skip

ABBREVIATIONS = {
    "sr": "senior", "snr": "senior", "jr": "junior", "jnr": "junior",
    "swe": "software engineer", "sde": "software development engineer",
    "eng": "engineer", "engr": "engineer", "dev": "developer", "mgr": "manager",
    "mgmt": "management", "ml": "machine learning", "ai": "ai", "qa": "quality assurance",
    "sre": "site reliability engineer", "ux": "ux", "ui": "ui", "vp": "vice president",
    "assoc": "associate", "admin": "administrator", "ops": "operations", "&": "and",
    "i": "i", "ii": "ii", "iii": "iii",
}  # fmt: skip

WORK_MODE_WORDS = {
    "remote", "hybrid", "onsite", "on-site", "in-office", "office", "wfh", "anywhere",
    "remote-first", "fully-remote", "distributed",
}  # fmt: skip

SENIORITY = {
    "intern", "internship", "trainee", "fresher", "graduate", "junior", "associate", "mid",
    "senior", "staff", "principal", "lead", "head", "director", "manager", "vice president",
    "chief", "i", "ii", "iii", "iv",
}  # fmt: skip

# Bracket words that never make a role different: regions, employment types, and the like.
FILLER = {
    "us", "uk", "eu", "emea", "apac", "latam", "india", "worldwide", "global", "only",
    "m/f/d", "f/m/d", "m/w/d", "w/m/d", "full-time", "fulltime", "part-time", "contract",
    "permanent", "temporary", "fixed-term", "new", "urgent", "and", "or", "in", "based",
}  # fmt: skip

REQUISITION = re.compile(r"\b(?:req|requisition|job|id)?\s*#?\s*[a-z]{0,3}-?\d{4,}\b")
BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")


def clean_company(name: str) -> str:
    """Acme Technologies Inc. -> acme."""
    words = re.findall(r"[a-z0-9]+", name.lower().replace("&", " and "))
    while len(words) > 1 and words[-1] in LEGAL:
        words.pop()
    return " ".join(words)


def _location_words(locations: list[str]) -> set[str]:
    out: set[str] = set()
    for loc in locations:
        for part in re.split(r"[,/|;·()\-]+", loc.lower()):
            part = part.strip()
            if part and part not in SENIORITY:
                out.add(part)
    return out


def canonical_role(title: str, locations: list[str] | None = None) -> str:
    """Sr. Backend Engineer (Remote) - Bangalore -> senior backend engineer.

    Brackets holding only a place, a work mode, a requisition number or an employment
    type are dropped; any other bracket is a specialization and stays, so Backend Engineer
    (Python) and Backend Engineer (Java) are two roles (the dedup QA set checks this)."""
    places = _location_words(locations or [])

    def bracket(m: re.Match[str]) -> str:
        inner = REQUISITION.sub(" ", m.group(0)[1:-1])
        tokens = set(re.findall(r"[a-z0-9/-]+", inner))
        return " " if tokens <= WORK_MODE_WORDS | places | FILLER else f" {inner} "

    text = REQUISITION.sub(" ", BRACKETS.sub(bracket, title.lower()))
    # Drop " - Bangalore", " | Remote", ", Remote (US)" segments that are only places or modes.
    parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+|\s*,\s*|\s*/\s*(?=remote)", text)]
    kept = []
    for i, part in enumerate(parts):
        tokens = set(re.findall(r"[a-z0-9-]+", part))
        is_place = part in places or (
            bool(tokens) and tokens <= (WORK_MODE_WORDS | places | FILLER)
        )
        if i > 0 and is_place:
            continue
        kept.append(part)
    words = re.findall(r"[a-z0-9+#.&-]+", " ".join(kept))
    out: list[str] = []
    for word in words:
        word = word.strip(".-")
        if not word or word in WORK_MODE_WORDS:
            continue
        out.extend(ABBREVIATIONS.get(word, word).split())
    return " ".join(out)


def seniority_of(role: str) -> frozenset[str]:
    words = role.split()
    found = {w for w in words if w in SENIORITY}
    if "vice" in words and "president" in words:
        found.add("vice president")
    return frozenset(found)
