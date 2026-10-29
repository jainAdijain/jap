"""Demo boards: watched_board.origin may be 'demo' (make demo, Phase 3 checklist).

Demo boards hold made-up postings for checking dedup by hand; runs never fetch them, and
make demo-clear may delete their postings (real postings are still never deleted, I6).

Revision ID: 0002_demo_boards
Revises: 0001_initial
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_demo_boards"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NO_DELETE = """
CREATE OR REPLACE FUNCTION posting_no_delete() RETURNS trigger AS $$
BEGIN
  IF %s THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'postings are closed, never deleted';
END $$ LANGUAGE plpgsql;
"""
DEMO_ONLY = (
    "EXISTS (SELECT 1 FROM watched_board b WHERE b.id = OLD.watched_board_id "
    "AND b.origin = 'demo')"
)


def upgrade() -> None:
    op.execute("ALTER TABLE watched_board DROP CONSTRAINT watched_board_origin_check")
    op.execute(
        "ALTER TABLE watched_board ADD CONSTRAINT watched_board_origin_check "
        "CHECK (origin IN ('seed','user','demo'))"
    )
    op.execute(NO_DELETE % DEMO_ONLY)


def downgrade() -> None:
    op.execute(NO_DELETE % "false")
    op.execute("DELETE FROM watched_board WHERE origin = 'demo'")
    op.execute("ALTER TABLE watched_board DROP CONSTRAINT watched_board_origin_check")
    op.execute(
        "ALTER TABLE watched_board ADD CONSTRAINT watched_board_origin_check "
        "CHECK (origin IN ('seed','user'))"
    )
