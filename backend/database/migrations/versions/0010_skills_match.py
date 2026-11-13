"""Skills match instead of a skills filter (PRD 1.21, F5, F6).

Skills never hide a job. Each listing stores how it fits my skills, written by the same SQL
pass as the filters (pipeline/matching.py): required_hit and nice_hit are my required and
nice-to-have skills the job asks for, and required_pct is the share of my required skills it
asks for, null when the job states no skills or I have none set (Skills not stated is
unknown, not 0).

profile.auto_apply_min_skills is the bar Automatic mode needs before it applies on its own
(F6), 60% unless changed on Settings.

A match rule saved before 1.21 that used skills keeps its other conditions: a combination
of skills alone is dropped, and a rule left empty becomes job title and location. The
stored listings are then filtered once with the cleaned rule (pipeline.matching, the same
statement a profile save runs), so jobs the skills condition used to hide are listed and
every job has its skills match as soon as this is applied.

Revision ID: 0010_skills_match
Revises: 0009_close_reason
Create Date: 2026-11-13
"""

from collections.abc import Sequence
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import op

from pipeline.matching import Criteria, refilter_sql

revision: str = "0010_skills_match"
down_revision: str | None = "0009_close_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Saved rules: drop skills from every combination, drop combinations left empty and repeats,
# and fall back to job title and location when nothing is left (tests/test_schema.py).
CLEAN_RULES = """
    UPDATE profile p SET match_rule = COALESCE(
      (SELECT jsonb_agg(combo ORDER BY first)
       FROM (SELECT combo, min(ord) AS first
             FROM (SELECT ord,
                     (SELECT COALESCE(jsonb_agg(c ORDER BY c), '[]'::jsonb)
                      FROM (SELECT DISTINCT c FROM jsonb_array_elements_text(e.combo) c
                            WHERE c IN ('role', 'location')) kept) AS combo
                   FROM jsonb_array_elements(p.match_rule) WITH ORDINALITY e(combo, ord)
                   WHERE jsonb_typeof(e.combo) = 'array') cleaned
             WHERE combo <> '[]'::jsonb
             GROUP BY combo) uniq),
      '[["location", "role"]]'::jsonb)
    WHERE jsonb_typeof(p.match_rule) = 'array';
"""


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE listing
          ADD COLUMN required_hit text[] NOT NULL DEFAULT '{}',
          ADD COLUMN nice_hit text[] NOT NULL DEFAULT '{}',
          ADD COLUMN required_pct smallint CHECK (required_pct BETWEEN 0 AND 100);
        ALTER TABLE profile
          ADD COLUMN auto_apply_min_skills smallint NOT NULL DEFAULT 60
          CHECK (auto_apply_min_skills BETWEEN 0 AND 100);
        """
    )
    op.execute(CLEAN_RULES)
    refilter_once()


def refilter_once() -> None:
    """Re-filter the stored listings with the cleaned rule, as a profile save would, so the
    jobs the old skills condition hid are listed, and every job has its skills match, the
    moment this migration is applied rather than after the next search. A database with no
    profile yet (a fresh one) has nothing to filter."""
    bind = op.get_bind()
    # work_modes is an enum array, which the driver hands back as text unless it is cast.
    profile = (
        bind.execute(
            sa.text(
                "SELECT target_roles, must_have_skills, nice_to_have_skills, locations,"
                " remote_regions, remote_any, match_rule, CAST(work_modes AS text[]) AS work_modes,"
                " excluded_companies, excluded_title_words FROM profile LIMIT 1"
            )
        )
        .mappings()
        .first()
    )
    if profile is None:
        return
    sql, params = refilter_sql(Criteria.of(SimpleNamespace(**profile)))
    bind.execute(sa.text(sql), params)


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE profile DROP COLUMN auto_apply_min_skills;
        ALTER TABLE listing
          DROP COLUMN required_pct, DROP COLUMN nice_hit, DROP COLUMN required_hit;
        """
    )
