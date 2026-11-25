"""The AI writer's answers as they came in, to count those I changed before sending (PRD
1.38, O26).

The answers of an application change as I edit them in Review, and an answer the writer
gave looks, once in, like one I typed. This keeps the writer's answers as imported, so when
the application is sent the trace can say how many of them I changed: the quality signal
for the writer that numbers alone can give.

Revision ID: 0016_writer_answers
Revises: 0015_writer_left_out
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_writer_answers"
down_revision: str | None = "0015_writer_left_out"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE application ADD COLUMN writer_answers jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE application DROP COLUMN writer_answers")
