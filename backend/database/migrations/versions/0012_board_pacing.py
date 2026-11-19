"""Pacing per job board and holds for review (V4 to V6, PRD 1.29, v0.23.0).

ats_hold keeps one row per ATS (Greenhouse, Lever, Ashby) that answered 429 or 403: until
when nothing is read or submitted there, and how many times in a row it happened (the hold
grows 1, 4, then 24 hours). A confirmed submit on that ATS clears it.

application.hold_reason is why an application must wait for my review whatever the mode:
the Claude writer's checker flagged it, or the form bans AI-written answers. Automatic mode
never sends one with a hold_reason on its own.

Revision ID: 0012_board_pacing
Revises: 0011_stated_work_mode
Create Date: 2026-11-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_board_pacing"
down_revision: str | None = "0011_stated_work_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ats_hold (
          ats ats PRIMARY KEY,
          until timestamptz NOT NULL,
          strikes int NOT NULL DEFAULT 0,
          reason text,
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE application ADD COLUMN hold_reason text;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE application DROP COLUMN hold_reason; DROP TABLE ats_hold;")
