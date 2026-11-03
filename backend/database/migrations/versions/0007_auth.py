"""Sign-in and accounts (PRD 1.18, S1 to S13, step 1): users, sessions, reset links.

app_user holds each account (S1), the first one the admin: the scrypt password hash, the
lockout counter (S2) and the two-step secret with its recovery codes (S3). The email check
(S10) and turning an account off (S12) come in step 2; their columns are here already. auth_session holds one row per signed-in browser
(S5) and per half-done two-step sign-in; only a SHA-256 of each cookie is stored, never the
cookie. password_reset holds the emailed reset links (S4), also only as hashes.

Revision ID: 0007_auth
Revises: 0006_remote_any
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_auth"
down_revision: str | None = "0006_remote_any"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE app_user (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email text NOT NULL,
            password_hash text NOT NULL,
            is_admin boolean NOT NULL DEFAULT false,
            email_verified_at timestamptz,
            disabled_at timestamptz,
            failed_sign_ins integer NOT NULL DEFAULT 0,
            locked_until timestamptz,
            totp_secret text,
            totp_pending_secret text,
            totp_last_step bigint,
            recovery_codes text[] NOT NULL DEFAULT '{}',
            password_changed_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE UNIQUE INDEX app_user_email ON app_user (lower(email))")
    op.execute("""
        CREATE TABLE auth_session (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
            token_hash text NOT NULL UNIQUE,
            stage text NOT NULL CHECK (stage IN ('signed_in', 'two_step')),
            code_tries integer NOT NULL DEFAULT 0,
            expires_at timestamptz NOT NULL,
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            user_agent text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX auth_session_user ON auth_session (user_id)")
    op.execute("""
        CREATE TABLE password_reset (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
            token_hash text NOT NULL UNIQUE,
            expires_at timestamptz NOT NULL,
            used_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE password_reset")
    op.execute("DROP TABLE auth_session")
    op.execute("DROP TABLE app_user")
