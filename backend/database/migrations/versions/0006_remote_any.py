"""All remote jobs (PRD 1.5, U16): profile.remote_any, and a stricter match rule for new users.

When remote_any is true, every remote job passes the Location condition whatever its regions
say (the "All remote jobs" choice on Job preferences). False keeps the Remote regions
behaviour. A profile created from now on starts with Matching "Job title and location fit"
(U17); a saved profile keeps its rule.

Revision ID: 0006_remote_any
Revises: 0005_hardening
Create Date: 2026-10-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_remote_any"
down_revision: str | None = "0005_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE profile ADD COLUMN remote_any boolean NOT NULL DEFAULT false")
    op.execute("""ALTER TABLE profile ALTER COLUMN match_rule SET DEFAULT '[["location", "role"]]'""")


def downgrade() -> None:
    op.execute(
        """ALTER TABLE profile ALTER COLUMN match_rule
           SET DEFAULT '[["role"], ["skills"], ["location"]]'"""
    )
    op.execute("ALTER TABLE profile DROP COLUMN remote_any")
