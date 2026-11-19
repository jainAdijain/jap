"""A company's own limit on applications from one person (JAP-13, v0.25.0).

Some employers cap how often one candidate may apply, and say so on the form: Supabase's
Ashby forms read "You can submit up to 3 applications within a 60-day period". Past the
limit the form still takes the submit but drops it without a word. JAP reads the limit off
the form and keeps it on the company, so it neither submits nor picks there again until the
window has room.

Revision ID: 0013_company_limit
Revises: 0012_board_pacing
Create Date: 2026-11-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_company_limit"
down_revision: str | None = "0012_board_pacing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE company ADD COLUMN apply_limit int, ADD COLUMN apply_limit_days int"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE company DROP COLUMN apply_limit, DROP COLUMN apply_limit_days")
