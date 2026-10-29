"""Filtered listing (F1 to F3): which listings match My profile, as one SQL statement.

A listing is listed when it satisfies at least one combination of the match rule (every
condition in that combination passes) and the always-on filters: its work mode is one I
ticked (or unknown, shown as Work mode unclear) and neither its company nor a title word is
excluded. The result is written to listing.matched (the conditions it met; null = not
listed) and listing.matched_role (the first target role it matches, for Jobs by role).
Nothing is fetched, so a profile change updates the list in well under a second.

Conditions:
- Role: the canonical role contains one of my target roles as whole words, after the same
  cleaning as dedup (Sr to Senior, locations dropped); engineer and developer count alike.
- Skills: every must-have skill is in the listing's skills. No must-have skills, or a job
  with no skills, fails.
- Location: a job location names one of my locations, or the job is remote and allows one
  of my Remote regions, or it is Remote - Worldwide (always counts).
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Profile
from pipeline.canonical import canonical_role, clean_company

CONDITIONS = ("role", "skills", "location")
DEFAULT_RULE = [["role"], ["skills"], ["location"]]
ROLE_SYNONYMS = {"engineer": ("engineer", "developer"), "developer": ("engineer", "developer")}
PLACE_SYNONYMS = [
    {"bangalore", "bengaluru"}, {"bombay", "mumbai"}, {"gurgaon", "gurugram"},
    {"madras", "chennai"}, {"calcutta", "kolkata"}, {"new delhi", "delhi"},
    {"us", "usa", "united states", "united states of america"},
    {"uk", "united kingdom", "great britain"}, {"nyc", "new york"},
    {"sf", "san francisco"}, {"uae", "united arab emirates"},
]  # fmt: skip
# Words that say "remote" rather than name a place; my Remote regions decide those jobs.
REMOTE_WORDS = {"remote", "anywhere", "worldwide", "global", "wfh", "work from home"}
WORLDWIDE = r"(^|[^a-z0-9])(worldwide|anywhere|global)([^a-z0-9]|$)"


class ProfileLike(Protocol):
    target_roles: list[str]
    must_have_skills: list[str]
    locations: list[str]
    remote_regions: list[str]
    match_rule: Any
    work_modes: Any
    excluded_companies: list[str]
    excluded_title_words: list[str]


@dataclass(frozen=True)
class Criteria:
    target_roles: tuple[str, ...] = ()
    must_have_skills: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    remote_regions: tuple[str, ...] = ()
    match_rule: tuple[tuple[str, ...], ...] = (("role",), ("skills",), ("location",))
    work_modes: tuple[str, ...] = ("remote", "hybrid", "onsite")
    excluded_companies: tuple[str, ...] = ()
    excluded_title_words: tuple[str, ...] = ()

    @classmethod
    def of(cls, p: ProfileLike) -> "Criteria":
        rule = tuple(
            tuple(c for c in CONDITIONS if c in combo) for combo in (p.match_rule or DEFAULT_RULE)
        )
        return cls(
            target_roles=tuple(p.target_roles or ()),
            must_have_skills=tuple(s.lower() for s in p.must_have_skills or ()),
            locations=tuple(p.locations or ()),
            remote_regions=tuple(p.remote_regions or ()),
            match_rule=tuple(c for c in rule if c) or (("role",), ("skills",), ("location",)),
            work_modes=tuple(str(m) for m in p.work_modes or ()),
            excluded_companies=tuple(p.excluded_companies or ()),
            excluded_title_words=tuple(p.excluded_title_words or ()),
        )


def _esc(word: str) -> str:
    """Escape a literal for a Postgres (and Python) regular expression."""
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", word)


def _whole(alternatives: Iterable[str]) -> str:
    alts = sorted({_esc(a) for a in alternatives if a}, key=len, reverse=True)
    return rf"(^|[^a-z0-9])({'|'.join(alts)})([^a-z0-9]|$)"


def role_pattern(role: str) -> str | None:
    """Senior Backend Engineer -> a pattern matching 'senior backend (engineer|developer)'."""
    words = (canonical_role(role) or role.lower()).split()
    if not words:
        return None
    parts = []
    for w in words:
        alts = ROLE_SYNONYMS.get(w, (w,))
        parts.append(_esc(alts[0]) if len(alts) == 1 else f"({'|'.join(map(_esc, alts))})")
    return rf"(^|[^a-z0-9])({' '.join(parts)})([^a-z0-9]|$)"


def place_pattern(place: str) -> str | None:
    """Bangalore -> matches Bangalore or Bengaluru as whole words. Remote words give None.

    A place typed with its region ("Bangalore, India", "Pune - Maharashtra") goes by its
    first part, the most specific one, so it still matches "Bengaluru, Karnataka, India".
    """
    parts = [" ".join(p.split()) for p in re.split(r"[,/|;()·]+|\s+-\s+", place.lower())]
    parts = [p for p in parts if p and p not in REMOTE_WORDS]
    if not parts:
        return None
    name = parts[0]
    names = {name}
    for group in PLACE_SYNONYMS:
        if name in group:
            names |= group
    return _whole(names)


def title_word_pattern(word: str) -> str | None:
    word = " ".join(word.lower().split())
    return _whole([word]) if word else None


def _rule_sql(rule: Sequence[Sequence[str]]) -> str:
    alias = {"role": "f.r", "skills": "f.s", "location": "f.g"}
    return " OR ".join("(" + " AND ".join(alias[c] for c in combo) + ")" for combo in rule)


def matches_sql(c: Criteria) -> tuple[str, dict[str, Any]]:
    """SELECT id, matched, matched_role for every listing (or those in :ids when given)."""
    roles = [(r, p) for r in c.target_roles if (p := role_pattern(r))]
    params: dict[str, Any] = {
        "role_patterns": [p for _, p in roles],
        "must": list(c.must_have_skills),
        "loc_patterns": [p for loc in c.locations if (p := place_pattern(loc))],
        "region_patterns": [p for r in c.remote_regions if (p := place_pattern(r))],
        "worldwide": WORLDWIDE,
        "modes": list(c.work_modes),
        "ex_names": [n.lower().strip() for n in c.excluded_companies],
        "ex_canon": [clean_company(n) for n in c.excluded_companies],
        "title_patterns": [p for w in c.excluded_title_words if (p := title_word_pattern(w))],
    }
    whens = []
    for i, (role, pattern) in enumerate(roles):
        params[f"rp{i}"], params[f"rn{i}"] = pattern, role
        whens.append(f"WHEN l.canonical_role ~ :rp{i} THEN CAST(:rn{i} AS text)")
    role_case = f"CASE {' '.join(whens)} END" if whens else "CAST(NULL AS text)"
    sql = f"""
    WITH f AS (
      SELECT l.id,
        COALESCE(l.canonical_role ~ ANY(CAST(:role_patterns AS text[])), false) AS r,
        (cardinality(CAST(:must AS text[])) > 0 AND l.skills @> CAST(:must AS text[])) AS s,
        (
          EXISTS (SELECT 1 FROM unnest(l.locations) loc
                  WHERE lower(loc) ~ ANY(CAST(:loc_patterns AS text[])))
          OR (
            (l.work_mode = 'remote'
             OR EXISTS (SELECT 1 FROM unnest(l.locations) loc WHERE lower(loc) ~ 'remote'))
            AND (
              EXISTS (SELECT 1 FROM unnest(l.remote_regions) rr WHERE lower(rr) ~ :worldwide)
              OR EXISTS (SELECT 1 FROM unnest(l.locations) loc WHERE lower(loc) ~ :worldwide)
              OR EXISTS (SELECT 1 FROM unnest(l.remote_regions) rr
                         WHERE lower(rr) ~ ANY(CAST(:region_patterns AS text[])))
              OR EXISTS (SELECT 1 FROM unnest(l.locations) loc
                         WHERE lower(loc) ~ 'remote'
                           AND lower(loc) ~ ANY(CAST(:region_patterns AS text[])))
            )
          )
        ) AS g,
        (
          (l.work_mode IS NULL OR CAST(l.work_mode AS text) = ANY(CAST(:modes AS text[])))
          AND NOT (lower(c.name) = ANY(CAST(:ex_names AS text[]))
                   OR c.canonical_name = ANY(CAST(:ex_canon AS text[])))
          AND NOT COALESCE(lower(l.title_display) ~ ANY(CAST(:title_patterns AS text[])), false)
        ) AS keep,
        {role_case} AS mrole
      FROM listing l JOIN company c ON c.id = l.company_id
      WHERE (CAST(:ids AS uuid[]) IS NULL OR l.id = ANY(CAST(:ids AS uuid[])))
    )
    SELECT f.id,
      CASE WHEN f.keep AND ({_rule_sql(c.match_rule)}) THEN array_remove(ARRAY[
        CASE WHEN f.r THEN 'role' END, CASE WHEN f.s THEN 'skills' END,
        CASE WHEN f.g THEN 'location' END], NULL) END AS matched,
      CASE WHEN f.keep AND ({_rule_sql(c.match_rule)}) THEN f.mrole END AS matched_role,
      f.keep AND f.r AS role_ok, f.keep AND f.s AS skills_ok, f.keep AND f.g AS location_ok
    FROM f
    """
    return sql, params


# Listed and shown by default: matched, not Closed, and on a board I still watch.
VISIBLE = """
  l.status <> 'closed'
  AND EXISTS (SELECT 1 FROM posting p JOIN watched_board b ON b.id = p.watched_board_id
              WHERE p.listing_id = l.id AND b.active)
"""


async def refilter(
    session: AsyncSession, criteria: Criteria, ids: Iterable[uuid.UUID] | None = None
) -> int:
    """Write matched and matched_role (all listings, or only ids). Returns how many are
    listed and visible now. The caller commits."""
    sql, params = matches_sql(criteria)
    params["ids"] = None if ids is None else list(ids)
    await session.execute(
        text(
            f"""
            UPDATE listing l SET matched = m.matched, matched_role = m.matched_role,
              updated_at = now()
            FROM ({sql}) m
            WHERE l.id = m.id
              AND (l.matched IS DISTINCT FROM m.matched
                   OR l.matched_role IS DISTINCT FROM m.matched_role)
            """
        ),
        params,
    )
    return await listed_count(session)


async def listed_count(session: AsyncSession) -> int:
    count = await session.scalar(
        text(f"SELECT count(*) FROM listing l WHERE l.matched IS NOT NULL AND {VISIBLE}")
    )
    return int(count or 0)


async def preview_counts(session: AsyncSession, criteria: Criteria) -> dict[str, int]:
    """How many listings would be listed with these settings, and how many pass each
    condition on its own (after work mode and exclusions), without saving anything."""
    sql, params = matches_sql(criteria)
    params["ids"] = None
    row = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE m.matched IS NOT NULL), "
                "count(*) FILTER (WHERE m.role_ok), count(*) FILTER (WHERE m.skills_ok), "
                f"count(*) FILTER (WHERE m.location_ok) FROM ({sql}) m "
                f"JOIN listing l ON l.id = m.id WHERE {VISIBLE}"
            ),
            params,
        )
    ).one()
    return dict(zip(("matching", "role", "skills", "location"), map(int, row), strict=True))


async def refilter_from_profile(
    session: AsyncSession, ids: Iterable[uuid.UUID] | None = None
) -> int:
    """refilter with the saved profile."""
    profile = await session.scalar(select(Profile))
    return await refilter(session, Criteria.of(profile) if profile else Criteria(), ids)
