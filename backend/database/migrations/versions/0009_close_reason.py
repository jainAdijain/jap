"""Why a posting is closed (PRD 1.20, I10).

posting.close_reason says whether the employer took the job down (gone_from_board, I6) or I
stopped following its company (unfollowed, I10). It is null while the posting is open.
Postings already closed were all closed because their board no longer listed them.

Postings left open by boards unfollowed before this version are not touched here: they are
closed once by `make close-unfollowed` (pipeline/unfollow.py), after a backup.

Revision ID: 0009_close_reason
Revises: 0008_observability
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_close_reason"
down_revision: str | None = "0008_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE posting ADD COLUMN close_reason text
          CHECK (close_reason IN ('gone_from_board', 'unfollowed'));
        UPDATE posting SET close_reason = 'gone_from_board' WHERE state = 'closed';
        ALTER TABLE posting ADD CONSTRAINT posting_close_reason_when_closed
          CHECK ((state = 'closed') = (close_reason IS NOT NULL));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE posting DROP CONSTRAINT posting_close_reason_when_closed;
        ALTER TABLE posting DROP COLUMN close_reason;
        """
    )
