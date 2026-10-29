"""Triggers (Phase 5, T2 to T5, U5): the profile_saved trigger and the Daily run time.

run_trigger gains profile_saved: a profile save re-filters at once, then fetches in the
background as its own run. profile.daily_run_at is when the Daily run starts in JAP_TZ;
06:00 by default, and changeable so the schedule can be tested a few minutes ahead.
profile.daily_run_last_at is when the Daily run last fired, so each slot fires once even
when its run merged into one already queued.

Revision ID: 0004_triggers
Revises: 0003_matched_role
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_triggers"
down_revision: str | None = "0003_matched_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE run_trigger ADD VALUE IF NOT EXISTS 'profile_saved'")
    op.execute("ALTER TABLE profile ADD COLUMN daily_run_at time NOT NULL DEFAULT '06:00'")
    op.execute("ALTER TABLE profile ADD COLUMN daily_run_last_at timestamptz")


def downgrade() -> None:
    # Postgres cannot drop an enum value; profile_saved stays in run_trigger.
    op.execute("ALTER TABLE profile DROP COLUMN daily_run_last_at")
    op.execute("ALTER TABLE profile DROP COLUMN daily_run_at")
