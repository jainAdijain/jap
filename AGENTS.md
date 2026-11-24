# AGENTS.md

JAP (Job Application Pipeline) watches the public job boards of companies you follow
(Greenhouse, Lever, Ashby), lists the jobs that match your profile, and prepares or submits
applications for you to review.

`README.md` is the long-form description of every behaviour and is kept current — read the
section that covers whatever you are changing before you change it. PRD ids (`A5`, `S6`, `H4`,
`O12`, `U8`) are used all over the code and in commit messages.

The PRD is the source of truth, with the engineering design doc under it. They live in `docs/`
and are **not in git**: this repo is public and they carry personal data.

- The documents are **Markdown files in `docs/`, edited in place** (since 23 Nov 2026). The
  `.md` is the document: there is no export to refresh and no copy elsewhere that outranks it.
  List `docs/` first rather than reaching for a fixed filename. Today the PRD is a `.md`; the
  engineering design doc and the hosting plan are still the PDF exports of 3 Nov 2026, with
  their text in `docs/text/` (a `pypdf` extraction: diagrams gone, tables flattened to one
  cell per line), and stay that way until each is brought over as a `.md`.
- `docs/archive/` holds what is no longer current: the PDFs and text a `.md` has replaced, and
  the build plan, retired on 23 Nov 2026 because all of its phases (0 to 8) are built. Never
  read it as current, and do not start a new build plan here without being asked.
- A change to the PRD raises its version: the line under the title and a new row at the top of
  its Version history, saying who asked. Scope changes go into the PRD first, then the code
  and `README.md`.
- The documents carry their own versions and they drift apart. Check the line under each title
  before you trust it: the design doc says which PRD version it follows and names the code
  version it describes. Where they disagree the PRD wins, so say that the older document is
  behind rather than quietly following it.
- If `docs/` is empty or absent you are on a machine without it. Work from `README.md`, and say
  that the PRD was unavailable rather than guessing at the meaning of a PRD id you cannot read.
- Never copy the contents of `docs/` into a tracked file, a commit message or a PR body.
- Two files in `docs/` are not exports but living lists that **every harness keeps current**
  (Claude, Codex, Gemini, opencode, Devin): `docs/ideas-pending.md` and `docs/failures.md`.
  See [Ideas and failures](#ideas-and-failures).

## Layout

```
Makefile            every job has one target; `make help` lists them
agent/              the AI harness runner (PRD 1.30): run.sh, job prompts, one adapter per
                    harness, contract schemas, launchd schedule, trace.py (the trace of each
                    run, PRD 1.33: numbers only, never text); agent/README.md maps it
infra/              compose.yaml (local), compose.release.yaml (live), docker/, caddy/,
                    env/, tunnel/, postgres/init.sql, scripts/
seed/               companies.yaml, skills.yaml
backend/            main.py, config.py, api/, schemas/, database/ (+migrations/), sources/,
                    pipeline/, llm/, functions/ (Inngest), observability/, auth/, tests/
frontend/           src/pages, src/components, src/api/client.ts
data/               gitignored: resumes, screenshots, cover letters, traces, backups
```

## Running and verifying

Everything runs in Docker. There is no host virtualenv and no host `node_modules` worth using —
run tools **inside the containers**, which the Makefile already does.

```
make start                  # build, start, wait for /api/health, migrate, check Ollama
make test                   # the full check: test-app + test-ui
make test-app               # ruff check . && ruff format --check . && mypy . && pytest -q
make test-ui                # npm run lint && npm run build
make logs / make ps
make migrate                # alembic upgrade head in the app container
make errors                 # last 20 warnings and errors from log_event
```

`make test` is what CI runs (`.github/workflows/ci.yml`), plus a `docker compose config` check of
`infra/compose.yaml`, the release overlay and the tunnel compose file. Run `make test` before
calling a change done. A single test:
`docker compose -p jap exec app pytest -q tests/test_matching.py -k remote`.

On the local stack `make test-app` ends with 6 failed submit tests (in `test_apply`,
`test_cover_letter`, `test_hardening` and `test_pacing`): they inherit the stack's
`JAP_SUBMIT_ENABLED=false`. They pass in CI, and here with
`docker compose -p jap exec -T app env -u JAP_SUBMIT_ENABLED pytest -q tests/test_apply.py ...`.
Any other failure is from your change.

Ollama is **not** in Docker — it runs natively on the machine that runs Docker (the VM today),
reached at `http://host.docker.internal:11434`. `/api/health` says whether the stack's Ollama is
up with `nomic-embed-text` pulled; `make ollama-check` asks the machine it is run on.

Services: ui 5173, app 8001 (`/api`), inngest 8290, db 5432, adminer 8080 — all bound to
127.0.0.1, all overridable with `JAP_*_PORT` in `.env`.

Every target takes `ENV=local|live`. Both run from this checkout: `local` is the dev stack
(`.env`, `data/`, never submits), `live` is the release stack behind the Cloudflare Tunnel at
jap.tutorial.monster (`.env.live`, `data-live/`, submits for real). Leave `ENV` off unless the
task is about hosting, and never run anything with `ENV=live` without being asked.

**Both stacks run on a VM, not on this Mac** (README, Hosting; PRD H1 to H9): live since
21 Nov 2026, and local too. The code is still edited here and copied to the VM, one way, every
two minutes, so do not edit on the VM. The local stack's ports are forwarded here, so
`localhost:5173` and `localhost:8001` answer as before, but this Mac's own Docker has no running
JAP stack:

- Run anything that talks to the containers with the VM as the Docker host:
  `DOCKER_HOST=ssh://oracle-vm make test`, and the same in front of a bare `docker compose`.
- `make start` builds from the source and mounts it, so it belongs on the VM, in its copy of
  the checkout. Do not start the stopped `jap` or `jap-live` containers here by hand.
- On a Mac `make start`, `restart` and `reset` with `ENV=live`, and `make tunnel-start`, refuse:
  live is in Automatic mode and a second one would submit the same applications twice. Do not
  pass `LIVE_ON_THIS_MAC=1`.

### Do not run without being asked

`make reset` (wipes the database), `make clean` (removes volumes and images), and anything that
touches `data/` (real resumes, screenshots, backups). All three prompt for confirmation; do not
answer the prompt on the user's behalf.

## Rules that must not be broken

These are enforced by tests and exist for good reasons. Breaking one is a bug even if everything
still passes locally.

- **No personal data reaches a model.** `backend/llm/redact.py` swaps names, emails, phones,
  addresses and profile links for `[NAME]`, `[EMAIL]` and the like, and a guard blocks any
  request that still carries one. Contact fields on application forms are filled by code, never
  by a model.
- **`backend/llm/client.py` is the only file that talks to OpenRouter.**
  `tests/test_isolation.py` greps the tree and fails otherwise. Every model call goes through
  `llm.call()`, which redacts, guards, enforces the spend cap, logs an `llm_call` row with cost,
  and validates the reply against a Pydantic model.
- **`backend/observability/trace.py` is the only file that talks to the trace service**
  (PRD 1.33; `tests/test_trace.py` checks it), and a trace never holds text: numbers, JAP's
  ids, values from fixed lists and short identifiers only. A new field goes on `FIELDS` there
  with the check its value must pass; never send a prompt, job text, an answer, a company or
  a title. The service's key (`JAP_TRACE_HEADERS`) is never given to a harness. The runner's
  trace file of a run passes the same list (`pipeline/agent_run_trace.py`, PRD 1.34). The
  codes a writer or checker may give in `reasons.json` (O24) are written in four places: the
  two job prompts, `agent/trace.py` and that allow-list; `make test-agent` fails when they
  differ, so add a code to all four.
- **Prompts are versioned files** in `backend/llm/prompts/<name>.v<N>.md`. Changing a prompt's
  behaviour means a new version file, not an edit in place. Each purpose has a `max_tokens` cap
  in `llm/call.py`.
- **Submit is pressed at most once.** If a retry finds it was pressed with no confirmation, the
  job becomes Failed with "Submit outcome unknown". `JAP_SUBMIT_ENABLED=false` (test) must never
  submit to a real job site.
- **Auth:** every `/api` route is behind a session except `/api/health` and `/api/auth/*`. New
  routers are registered in `main.py` inside the `dependencies=[Depends(current_user)]` loop.
  `/api/inngest` answers only direct calls from inside Docker.
- **Logs are scrubbed** (`backend/observability/scrub.py`): emails, phone numbers, keys, tokens
  and security codes are replaced, and answers, resume text and cover letters are never written
  at all. Keep it that way when adding log lines.
- **Secrets:** `.env` and `.env.live` are gitignored and hold the OpenRouter key, the trace service's key and the mailbox
  password. Never commit one, never echo its values into output, never add a real key to
  `.env.example`. Concretely, and without exception:
  - Do not open `.env`, `.env.*` or `.env.tunnel` with the read tool, `cat`, `head` or an editor.
    To check whether a key is set, test it and print only `set`/`empty`
    (`grep -qE '^KEY=.+' .env && echo set`), never the line.
  - Do not print rendered configuration. `docker compose config` substitutes every variable, so
    its output contains the live secrets: use `config -q`, which validates and prints nothing.
    When a field must be inspected, parse to JSON and select only that field.
  - Never grep or filter output that may contain a secret. A pattern matches substrings, and a
    port or id can appear inside a random key.
  - Copy values between env files with a script that writes them, never one that prints them.

## Conventions

### Backend (Python 3.11, FastAPI, async SQLAlchemy 2.0)

- ruff: line length 100, `select = ["E", "F", "I", "B", "UP", "ASYNC"]`, plus `ruff format`.
  mypy runs **strict** — annotate everything, including test functions (`-> None`).
- One router per file in `backend/api/<name>.py`:
  `router = APIRouter(prefix="/boards", tags=["boards"])`, async handlers taking the
  `Session` dependency from `database.session`, returning Pydantic models from
  `backend/schemas/`. Errors are `raise ApiError(status, "snake_code", "Sentence for the user")`
  from `api.errors`.
- Inngest functions live in `backend/functions/`, one per file, registered in
  `functions/__init__.py` as `ALL_FUNCTIONS`. Business logic belongs in `backend/pipeline/`;
  the function file is the step wiring.
- Settings are fields on `Settings` in `backend/config.py` (pydantic-settings, read from
  `.env`). Every new setting also goes in `.env.example` with a comment saying what it does.
- Migrations are Alembic, numbered in order: `backend/database/migrations/versions/000N_name.py`.
  Both ruff and mypy exclude that folder. Apply with `make migrate`.
- Module docstrings explain *why* and cite the PRD id. Follow that; the codebase is unusually
  well commented and a bare file stands out.

### Frontend (React 19, Vite, TypeScript)

- No CSS framework and no state library on purpose: one `src/styles.css`, and
  `src/store.tsx` + context files for state. Do not add Tailwind, Redux, react-query or a
  component kit.
- All HTTP goes through `src/api/client.ts`, which owns `credentials: 'include'`, the
  `ApiError` shape and the `jap:signed-out` event. Types for API payloads are declared there
  and imported by pages.
- One file per screen in `src/pages/`. Shared pieces only go in `src/components/` once a second
  page needs them.
- `npm run lint` is eslint flat config; `npm run build` is `tsc -b && vite build` and type
  errors fail it. There is no UI test runner.

### Tests

- pytest with `asyncio_mode = "auto"`; `testpaths = ["tests"]`.
- Database tests run against a separate `<db>_test` database, created and migrated once per
  session. Without a reachable Postgres they skip, unless `JAP_REQUIRE_DB=1` (CI), where they
  fail.
- Fixtures in `tests/conftest.py`: `db` (clean database + temp `data/`), `sql` (raw psycopg
  connection), `anon` (httpx client, no session), `api` (signed in as the owner), `events`
  (captures Inngest sends instead of sending them).
- `make qa` places the 106 labelled pairs in `tests/qa/dedup_pairs.yaml` through the real dedup;
  CI fails below 95% accuracy or on any wrong merge. Touching
  `pipeline/dedup.py` or `pipeline/canonical.py` means running it.

## Bugs and issues

`BUGS.md` is the numbered list of known defects, in one serial order that is never renumbered
and never reused. Read it at the start of a session, before reporting anything as new.

Aditya says whether something is a **bug** or an **issue**; you give it the next free `JAP-N`,
write it up well enough to act on weeks later (symptom, where it shows, root cause if known,
the files involved, what the fix would be) and keep the status current. The `JAP-` prefix
avoids GitHub turning a bare `#2` into a link to pull request 2.

Cite the id in commit subjects next to the version — `Fix the enrich retry storm (v0.17.0,
JAP-2)` — and close the entry in the same commit that fixes it.

## Ideas and failures

Two private lists in `docs/`, one of each, shared by every harness. Read both at the start of a
session, update them in the same session the idea or the failure comes up, and do not start a
second file for either anywhere else.

- **`docs/ideas-pending.md`** is the ideas list (it replaced the ideas PDF on 23 Nov 2026).
  When Aditya raises an idea, add it there: a row in "Later (v2)" with who raised it and when,
  a section of its own when a row is not enough, a version bump and a line in its Version
  history. Never delete an idea; change its status. An idea becomes work only once it is in
  the PRD.
- **`docs/failures.md`** is the failures log: applications that failed on live and runs that
  broke, with the reason, the root cause, the status and the lessons. When you find a failed
  application or a broken run, add or update its row there and the "Last updated" line.
  `agent/run.sh` hands a copy to every reader, writer and checker session, so it must never
  hold personal data (no name, email, phone, address, resume text or answers) and its Lessons
  stay short. A defect in JAP still gets its `JAP-N` in `BUGS.md` as above; the failures row
  cites the id.

Both name companies and jobs, so they stay out of git like the rest of `docs/`: do not quote
them in a tracked file, a commit message or a PR body.

## Releases

A user-visible change gets a version bump in three places, kept in step:

1. the `**Version X.Y.Z (title), D Mon YYYY.**` line at the top of `README.md`,
2. a new row at the top of the Version history table in `README.md`,
3. `app = FastAPI(title="JAP", version="X.Y.Z")` in `backend/main.py`.

`backend/pyproject.toml` and `frontend/package.json` stay at `0.1.0` — they are not the product
version.

Commit subjects are a plain sentence saying what changed, with the version or PRD id in
brackets: `Logs, Health and Insights (v0.14.0, PRD 1.19 Part A)`,
`Fix OpenRouter 402 (v0.11.1) ... (#13)`. No Conventional Commits prefixes.
