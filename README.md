# JAP: Job Application Pipeline

**Version 0.3.0 (Phase 2), 29 Oct 2026.**

JAP watches the public job boards of the companies you pick (Greenhouse, Lever and Ashby),
lists the jobs that match your profile, and prepares or submits applications for you to review.
The [JAP PRD](https://claude.ai/artifact/WL9RYjsPDCTmknMLRqzKRj) is the single source of truth;
the [build plan](https://claude.ai/artifact/1TrqC2jt8SuPVbULa8i6eU) and the
[engineering design doc](https://claude.ai/artifact/XsWXaEvBdEsFf97GguqtAy) follow it.

## What runs

| Service | URL (127.0.0.1 only) | What it is |
| --- | --- | --- |
| ui | http://localhost:5173 | React + Vite UI: My profile, and a Dashboard with the status checks |
| app | http://localhost:8001 | FastAPI JSON API and Inngest functions (`/api/health`, `/api/inngest`) |
| inngest | http://localhost:8290 | Inngest dev server, runs every pipeline step |
| db | localhost:5432 | Postgres 16 with pgvector, pg_trgm, pgcrypto |
| adminer | http://localhost:8080 | Database browser (server `db`, user/password/db `jap`) |

Each host port can be changed in `.env` (`JAP_UI_PORT`, `JAP_API_PORT`, `JAP_INNGEST_PORT`,
`JAP_DB_PORT`, `JAP_ADMINER_PORT`). The API and Inngest use 8001 and 8290 so they don't clash with
other projects on 8000 and 8288-8289 (an Inngest dev server publishes both). Inside Docker the services keep their usual ports.

Ollama is **not** in Docker. It runs natively on the Mac so it can use the GPU, and the app
reaches it at `http://host.docker.internal:11434`.

## First run (macOS)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and [Ollama](https://ollama.com/download).
2. Pull the embedder: `ollama pull nomic-embed-text`
3. `make start` creates `.env` from `.env.example` the first time. Put your OpenRouter key and
   Gmail app password in `.env` (it is gitignored; never commit it).
4. Open http://localhost:5173: API, Database, Inngest and Ollama should all show OK.

## Personal data and models

No name, email, phone, address or profile link is sent to any model. `backend/llm/redact.py`
swaps them for placeholders such as `[NAME]`, a guard blocks any request that still contains
one, and `backend/llm/client.py` is the only code that talks to OpenRouter (a test enforces
it). Each call is logged in `llm_call` with its cost; model steps stop once this month's
spend reaches `JAP_MONTHLY_COST_CAP_USD`.

## Fetching jobs

Add companies in My profile by pasting their Greenhouse, Lever or Ashby board URL (or typing
the name), then press **Run: find only** on the Dashboard. Each board is one `fetch-board` run
in Inngest, throttled to one request a second per ATS. Job descriptions are read by the
extract model once per description: a re-run with unchanged postings costs nothing. The
first run on a big board (some list 700+ jobs) makes one model call per job, so start with a
few companies and watch the AI spend in My profile.

## Make targets

| Target | Does |
| --- | --- |
| `make start` | Build and start the stack, wait for `/api/health`, apply migrations, check Ollama |
| `make stop` | Stop the stack (data kept) |
| `make reset` | Asks first, then wipes the database: down -v, up, migrate, seed |
| `make clean` | Asks first, then removes containers, volumes, JAP images and build caches; `data/` is kept |
| `make logs` / `make ps` | Follow logs / list services |
| `make migrate` | `alembic upgrade head` in the app container |
| `make seed` | Load `seed/companies.yaml` and `seed/skills.yaml` |
| `make test` | ruff, mypy, pytest for app; lint and build for ui |
| `make backup` / `make restore FILE=...` | Dump to / restore from `data/backups/` |
| `make llm-check` | One tiny test call to each OpenRouter model (no personal data) |
| `make ollama-check` | Is Ollama answering with `nomic-embed-text` pulled |

## Layout

```
Makefile            .env.example
infra/              compose.yaml, docker/ (Dockerfiles), postgres/init.sql, scripts/
seed/               companies.yaml, skills.yaml
backend/            main.py, config.py, schemas/, database/ (+ migrations/), api/,
                    sources/, pipeline/, llm/, functions/ (Inngest), tests/
frontend/           src/pages, src/components, src/api
data/               gitignored: resumes, screenshots, cover letters, backups
.github/workflows/  CI: ruff, mypy, pytest; ui lint and build; compose config check
```

## Version history

| Version | Date | Changes |
| --- | --- | --- |
| 0.3.0 | 29 Oct 2026 | Phase 2: Greenhouse, Lever and Ashby adapters, board detection from a URL or name, Find only runs (run-pipeline, fetch-board, enrich-posting), extraction cached by description hash, Closed postings, job list with the JD side panel |
| 0.2.2 | 29 Oct 2026 | My profile: "No unsaved changes" next to a disabled Save, an inline hint when no work mode is ticked, and work modes compared as a set |
| 0.2.1 | 29 Oct 2026 | Writer model is `anthropic/claude-sonnet-4.5` (normal calls); OpenRouter only takes `:batch` ids through its batch service |
| 0.2.0 | 29 Oct 2026 | Phase 1: full schema (0001_initial), My profile, resume upload and parsing, redact module, model spend log and $2 cap, Companies to watch, answer bank |
| 0.1.2 | 29 Oct 2026 | Inngest host port 8290, since another Inngest dev server also takes 8289 |
| 0.1.1 | 29 Oct 2026 | Host ports set in `.env`; API on 8001 and Inngest on 8289 by default |
| 0.1.0 | 29 Oct 2026 | Phase 0: repo layout, Docker Compose (ui, app, inngest, db, adminer), Makefile, FastAPI health and Inngest endpoint, status page, CI |
