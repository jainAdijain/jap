"""Logging, observability and analytics, Part A (PRD 1.19, O1 to O12).

- log_event: warnings and errors, plus the step lines an application timeline needs (O3, O8).
  Kept 30 days (functions/health_watch.py deletes older rows).
- route_stat: one row per API route per hour with count, errors and time taken (O4, O5).
- alert_state: when each health alert was last emailed, so it goes at most once a day (O6).
- llm_call.application_id: the application a model call was made for (O8). llm_call.run_id
  existed but was never written; from now on both come from the log context.

user_id columns are nullable and have no foreign key yet: sign-in step 2 (Part B) fills them.

Revision ID: 0008_observability
Revises: 0007_auth
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_observability"
down_revision: str | None = "0007_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE log_event (
            id bigserial PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            level text NOT NULL,
            event text NOT NULL,
            message text NOT NULL,
            logger text NOT NULL,
            request_id text,
            user_id uuid,
            run_id uuid,
            application_id uuid,
            posting_id uuid,
            inngest_run_id text,
            fields jsonb NOT NULL DEFAULT '{}'
        )
        """
    )
    op.execute("CREATE INDEX log_event_created_at ON log_event (created_at)")
    op.execute("CREATE INDEX log_event_application ON log_event (application_id, created_at)")
    op.execute("CREATE INDEX log_event_level ON log_event (level, created_at)")
    op.execute(
        """
        CREATE TABLE route_stat (
            hour timestamptz NOT NULL,
            method text NOT NULL,
            route text NOT NULL,
            count integer NOT NULL DEFAULT 0,
            errors integer NOT NULL DEFAULT 0,
            total_ms bigint NOT NULL DEFAULT 0,
            max_ms integer NOT NULL DEFAULT 0,
            PRIMARY KEY (hour, method, route)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE alert_state (
            key text PRIMARY KEY,
            last_sent_at timestamptz NOT NULL,
            detail text
        )
        """
    )
    op.execute("ALTER TABLE llm_call ADD COLUMN application_id uuid")
    op.execute("CREATE INDEX llm_call_application ON llm_call (application_id)")


def downgrade() -> None:
    op.execute("DROP INDEX llm_call_application")
    op.execute("ALTER TABLE llm_call DROP COLUMN application_id")
    op.execute("DROP TABLE alert_state")
    op.execute("DROP TABLE route_stat")
    op.execute("DROP TABLE log_event")
