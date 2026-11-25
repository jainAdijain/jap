"""What the AI writer last left out, so it is not handed the same job again (PRD 1.37, W13).

A job the writer leaves out stays at Needs you, and before 1.37 every writer run handed it
out again with the same resume, answer bank and questions, and paid for the same answer:
on 24 Nov 2026 four of six jobs came back left out for a fact only I know. The import now
notes, for each job handed out and not answered, when and on which inputs (a hash of the
resume, the answer bank, the job's open questions and its description); the export skips
the job while those inputs are the same, for 7 days at most.

Revision ID: 0015_writer_left_out
Revises: 0014_llm_call_trace
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_writer_left_out"
down_revision: str | None = "0014_llm_call_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE application ADD COLUMN writer_left_out_at timestamptz,"
        " ADD COLUMN writer_left_out_inputs text"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE application DROP COLUMN writer_left_out_at,"
        " DROP COLUMN writer_left_out_inputs"
    )
