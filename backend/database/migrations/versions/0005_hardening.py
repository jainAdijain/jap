"""Hardening (Phase 8): spend cap on My profile, why an automatic job waits, alerts, form tests.

profile.spend_cap_usd is this month's model spend cap, set on My profile; NULL means
JAP_MONTHLY_COST_CAP_USD ($2). application.review_reason says why an Automatic mode job
still waits at Ready for review (A3). run.alert_sent_at and application.alert_sent_at make
the failure email go once. form_check keeps the weekly live form test per ATS.

Revision ID: 0005_hardening
Revises: 0004_triggers
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_hardening"
down_revision: str | None = "0004_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE profile ADD COLUMN spend_cap_usd numeric(8,2)")
    op.execute("ALTER TABLE application ADD COLUMN review_reason text")
    op.execute("ALTER TABLE application ADD COLUMN alert_sent_at timestamptz")
    op.execute("ALTER TABLE run ADD COLUMN alert_sent_at timestamptz")
    op.execute(
        """
        CREATE TABLE form_check (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          ats ats NOT NULL,
          posting_id uuid REFERENCES posting(id),
          url text,
          ok boolean NOT NULL,
          fields int NOT NULL DEFAULT 0,
          required int NOT NULL DEFAULT 0,
          error text,
          screenshot_path text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX form_check_latest ON form_check (ats, created_at DESC);
        CREATE TRIGGER form_check_touch BEFORE UPDATE ON form_check
          FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE form_check")
    op.execute("ALTER TABLE run DROP COLUMN alert_sent_at")
    op.execute("ALTER TABLE application DROP COLUMN alert_sent_at")
    op.execute("ALTER TABLE application DROP COLUMN review_reason")
    op.execute("ALTER TABLE profile DROP COLUMN spend_cap_usd")
