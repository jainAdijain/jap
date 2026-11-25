"""A hold on one company whose own careers site refused JAP (PRD 1.39, V5, v0.34.0).

Some companies list their jobs on Greenhouse but send applicants to a form on their own
careers site. When that site answered 403, ats_hold put the whole of Greenhouse on hold, so
one company's site stopped every other Greenhouse company for 1 to 24 hours. company_hold
keeps such a refusal on the company that refused, with the same strikes and the same 1, 4
and 24 hours; ats_hold stays for refusals from the board's own pages.

Revision ID: 0017_company_hold
Revises: 0016_writer_answers
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_company_hold"
down_revision: str | None = "0016_writer_answers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE company_hold (
          company_id uuid PRIMARY KEY REFERENCES company (id) ON DELETE CASCADE,
          until timestamptz NOT NULL,
          strikes int NOT NULL DEFAULT 0,
          host text,
          reason text,
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE company_hold")
