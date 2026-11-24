"""What a model call JAP makes itself records about how it went (PRD 1.33, O19, v0.28.0).

Without a harness each model call is one llm_call row. It already had the purpose, model,
prompt version, tokens, cost, time taken, and the run and application. O19 adds the posting
it belongs to, the provider that served it, which attempt it was, cached and reasoning
tokens, and how it ended: valid, retried, blocked, spend_cap or error. Rows from before
keep these empty.

Revision ID: 0014_llm_call_trace
Revises: 0013_company_limit
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_llm_call_trace"
down_revision: str | None = "0013_company_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = {
    "posting_id": "uuid",
    "provider": "text",
    "attempt": "int",
    "tokens_cached": "int",
    "tokens_reasoning": "int",
    "outcome": "text",
}


def upgrade() -> None:
    added = ", ".join(f"ADD COLUMN {name} {kind}" for name, kind in COLUMNS.items())
    op.execute(f"ALTER TABLE llm_call {added}")


def downgrade() -> None:
    op.execute("ALTER TABLE llm_call " + ", ".join(f"DROP COLUMN {name}" for name in COLUMNS))
