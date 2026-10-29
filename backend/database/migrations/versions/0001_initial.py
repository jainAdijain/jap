"""Initial schema: the PRD's eight tables plus six support tables (design doc v1.5).

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = [
    "profile", "resume_version", "company", "watched_board", "listing", "posting",
    "run", "application", "answer_bank", "company_alias", "skill_alias",
    "dedup_override", "possible_duplicate", "llm_call",
]

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE ats            AS ENUM ('greenhouse','lever','ashby');
CREATE TYPE work_mode      AS ENUM ('remote','hybrid','onsite');
CREATE TYPE apply_mode     AS ENUM ('review','automatic');
CREATE TYPE posting_state  AS ENUM ('open','closed');
CREATE TYPE listing_status AS ENUM ('not_applied','ready_for_review','applying','applied',
                                    'needs_input','failed','manual_apply','skipped','closed');
CREATE TYPE application_status AS ENUM ('preparing','ready_for_review','applying','applied',
                                        'needs_input','failed','cancelled');
CREATE TYPE run_trigger    AS ENUM ('resume_updated','manual','scheduled');
CREATE TYPE run_mode       AS ENUM ('find_only','find_and_apply');
CREATE TYPE run_status     AS ENUM ('queued','running','succeeded','failed');

CREATE TABLE profile (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  singleton boolean NOT NULL DEFAULT true UNIQUE CHECK (singleton),
  full_name text, email text, phone text, address text,
  links jsonb NOT NULL DEFAULT '[]',
  target_roles text[] NOT NULL DEFAULT '{}',
  must_have_skills text[] NOT NULL DEFAULT '{}',
  nice_to_have_skills text[] NOT NULL DEFAULT '{}',
  locations text[] NOT NULL DEFAULT '{}',
  remote_regions text[] NOT NULL DEFAULT '{}',
  match_rule jsonb NOT NULL DEFAULT '[["role"],["skills"],["location"]]',
  work_modes work_mode[] NOT NULL DEFAULT '{remote,hybrid,onsite}'
    CHECK (cardinality(work_modes) >= 1),
  excluded_companies text[] NOT NULL DEFAULT '{}',
  excluded_title_words text[] NOT NULL DEFAULT '{}',
  tone_note text,
  apply_mode apply_mode NOT NULL DEFAULT 'review',
  daily_run boolean NOT NULL DEFAULT false,
  daily_cap int NOT NULL DEFAULT 20 CHECK (daily_cap BETWEEN 0 AND 100)
);

CREATE TABLE resume_version (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  file_path text NOT NULL,
  file_sha256 char(64) NOT NULL,
  original_name text NOT NULL,
  mime text NOT NULL CHECK (mime IN ('application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
  text text,
  parsed jsonb,
  parse_error text,
  accepted_at timestamptz,
  is_active boolean NOT NULL DEFAULT false,
  uploaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_resume ON resume_version (is_active) WHERE is_active;

CREATE TABLE company (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  canonical_name text NOT NULL UNIQUE,
  careers_domain text UNIQUE
);

CREATE TABLE watched_board (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ats ats NOT NULL, board_token text NOT NULL,
  display_name text NOT NULL,
  company_id uuid REFERENCES company(id),
  origin text NOT NULL CHECK (origin IN ('seed','user')),
  active boolean NOT NULL DEFAULT true,
  last_checked_at timestamptz, last_ok boolean,
  UNIQUE (ats, board_token)
);

CREATE TABLE listing (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES company(id),
  canonical_role text NOT NULL,
  title_display text NOT NULL,
  locations text[] NOT NULL DEFAULT '{}',
  work_mode work_mode,
  remote_regions text[] NOT NULL DEFAULT '{}',
  skills text[] NOT NULL DEFAULT '{}',
  seniority text,
  primary_posting_id uuid,
  status listing_status NOT NULL DEFAULT 'not_applied',
  matched text[],
  first_posted_at timestamptz, last_posted_at timestamptz,
  applied_at timestamptz, closed_at timestamptz,
  UNIQUE (company_id, canonical_role)
);
CREATE INDEX listing_dashboard ON listing (status, last_posted_at DESC) WHERE matched IS NOT NULL;
CREATE INDEX listing_skills ON listing USING gin (skills);
CREATE INDEX listing_locations ON listing USING gin (locations);

CREATE TABLE posting (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source ats NOT NULL,
  external_id text NOT NULL,
  watched_board_id uuid NOT NULL REFERENCES watched_board(id),
  listing_id uuid REFERENCES listing(id),
  company_name_raw text NOT NULL, title_raw text NOT NULL,
  canonical_role text,
  requisition_id text,
  locations text[] NOT NULL DEFAULT '{}',
  department text, employment_type text,
  posted_at timestamptz,
  posting_url text NOT NULL, apply_url text NOT NULL,
  description_html text NOT NULL,
  description_text text NOT NULL,
  description_hash char(64) NOT NULL,
  raw_payload jsonb NOT NULL,
  extracted jsonb,
  extracted_hash char(64),
  embedding vector(768),
  embedded_hash char(64),
  state posting_state NOT NULL DEFAULT 'open',
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz,
  UNIQUE (source, external_id)
);
CREATE INDEX posting_listing ON posting (listing_id);
CREATE INDEX posting_board_open ON posting (watched_board_id) WHERE state = 'open';
CREATE INDEX posting_embedding ON posting USING hnsw (embedding vector_cosine_ops);
CREATE INDEX posting_title_trgm ON posting USING gin (canonical_role gin_trgm_ops);
ALTER TABLE listing ADD FOREIGN KEY (primary_posting_id) REFERENCES posting(id)
  DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE run (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trigger run_trigger NOT NULL,
  mode run_mode NOT NULL,
  scope jsonb NOT NULL DEFAULT '{}',
  status run_status NOT NULL DEFAULT 'queued',
  started_at timestamptz, ended_at timestamptz,
  counts jsonb NOT NULL DEFAULT '{}',
  error text,
  inngest_run_id text
);
-- One run at a time, repeats collapse (T4): at most one queued run.
CREATE UNIQUE INDEX one_queued_run ON run ((true)) WHERE status = 'queued';

CREATE TABLE application (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  listing_id uuid NOT NULL REFERENCES listing(id),
  posting_id uuid NOT NULL REFERENCES posting(id),
  run_id uuid REFERENCES run(id),
  status application_status NOT NULL DEFAULT 'preparing',
  mode apply_mode NOT NULL,
  resume_version_id uuid NOT NULL REFERENCES resume_version(id),
  jd_snapshot jsonb,
  captured_at timestamptz,
  form_fields jsonb,
  answers jsonb,
  cover_letter_text text, cover_letter_path text,
  claim_check jsonb,
  pending_questions jsonb,
  screenshot_path text, trace_path text,
  error text, attempts int NOT NULL DEFAULT 0,
  submit_started_at timestamptz,
  submitted_at timestamptz, email_sent_at timestamptz,
  inngest_run_id text
);
CREATE UNIQUE INDEX one_applied_per_listing ON application (listing_id) WHERE status = 'applied';
CREATE UNIQUE INDEX one_active_attempt ON application (listing_id)
  WHERE status IN ('preparing','ready_for_review','applying','needs_input');

CREATE TABLE answer_bank (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question_pattern text NOT NULL,
  question_norm text NOT NULL UNIQUE,
  answer text NOT NULL,
  last_used_at timestamptz
);
CREATE INDEX answer_bank_trgm ON answer_bank USING gin (question_norm gin_trgm_ops);

CREATE TABLE company_alias (
  alias text PRIMARY KEY,
  company_id uuid NOT NULL REFERENCES company(id),
  origin text NOT NULL CHECK (origin IN ('auto','user'))
);

CREATE TABLE skill_alias (
  alias text PRIMARY KEY,
  canonical text NOT NULL
);

CREATE TABLE dedup_override (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL CHECK (kind IN ('merge','split')),
  posting_source ats NOT NULL, posting_external_id text NOT NULL,
  listing_id uuid REFERENCES listing(id),
  UNIQUE (posting_source, posting_external_id)
);

CREATE TABLE possible_duplicate (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL CHECK (kind IN ('company','listing')),
  a_id uuid NOT NULL, b_id uuid NOT NULL CHECK (a_id < b_id),
  scores jsonb NOT NULL,
  decision text NOT NULL DEFAULT 'open' CHECK (decision IN ('open','merged','kept_apart')),
  UNIQUE (kind, a_id, b_id)
);

CREATE TABLE llm_call (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  purpose text NOT NULL,
  model text NOT NULL, prompt_version text NOT NULL,
  input_sha256 char(64) NOT NULL,
  blocked boolean NOT NULL DEFAULT false,
  tokens_in int, tokens_out int, cost_usd numeric(10,6), latency_ms int,
  error text, run_id uuid REFERENCES run(id)
);

CREATE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

RULES = """
CREATE INDEX llm_call_month ON llm_call (created_at);

-- Applied is frozen (A2): only email_sent_at and updated_at may change; no delete.
CREATE FUNCTION application_frozen() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status = 'applied' THEN
      RAISE EXCEPTION 'application % is Applied and cannot be deleted', OLD.id;
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status = 'applied' AND
     (to_jsonb(NEW) - 'email_sent_at' - 'updated_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'email_sent_at' - 'updated_at') THEN
    RAISE EXCEPTION 'application % is Applied and frozen', OLD.id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER application_frozen BEFORE UPDATE OR DELETE ON application
  FOR EACH ROW EXECUTE FUNCTION application_frozen();

-- Closed, not deleted (I6): no code path deletes a posting.
CREATE FUNCTION posting_no_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'postings are closed, never deleted';
END $$ LANGUAGE plpgsql;
CREATE TRIGGER posting_no_delete BEFORE DELETE ON posting
  FOR EACH ROW EXECUTE FUNCTION posting_no_delete();
"""

DOWNGRADE = """
DROP TABLE IF EXISTS llm_call, possible_duplicate, dedup_override, skill_alias, company_alias,
  answer_bank, application, run, posting, listing, watched_board, company, resume_version,
  profile CASCADE;
DROP FUNCTION IF EXISTS application_frozen, posting_no_delete, touch_updated_at;
DROP TYPE IF EXISTS run_status, run_mode, run_trigger, application_status, listing_status,
  posting_state, apply_mode, work_mode, ats;
"""


def _timestamps(table: str) -> str:
    return f"""
ALTER TABLE {table}
  ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
CREATE TRIGGER {table}_touch BEFORE UPDATE ON {table}
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
"""


def upgrade() -> None:
    op.execute(SCHEMA)
    # created_at and updated_at on every table (build plan convention).
    for table in TABLES:
        op.execute(_timestamps(table))
    op.execute(RULES)


def downgrade() -> None:
    op.execute(DOWNGRADE)
