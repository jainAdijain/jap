"""listing.matched_role: the first target role a listing matches (Phase 4, U2 and F2).

matched already holds the conditions a listing met (null = not listed). matched_role is
the target role it counts under in Jobs by role and the role filter; null means it is
listed through Skills or Location only.

Revision ID: 0003_matched_role
Revises: 0002_demo_boards
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_matched_role"
down_revision: str | None = "0002_demo_boards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE listing ADD COLUMN matched_role text")
    op.execute(
        "CREATE INDEX listing_matched_role ON listing (matched_role) WHERE matched IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS listing_matched_role")
    op.execute("ALTER TABLE listing DROP COLUMN matched_role")
