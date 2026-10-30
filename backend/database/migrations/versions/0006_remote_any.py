"""All remote jobs (PRD 1.5, U16): profile.remote_any.

When true, every remote job passes the Location condition whatever its regions say (the
"All remote jobs" choice on Job preferences). False keeps the Remote regions behaviour.

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


def downgrade() -> None:
    op.execute("ALTER TABLE profile DROP COLUMN remote_any")
