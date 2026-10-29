# JAP: Job Application Pipeline

**Version 0.9.0 (Phase 8), 29 Oct 2026.**

JAP watches the public job boards of the companies you pick (Greenhouse, Lever and Ashby),
lists the jobs that match your profile, and prepares or submits applications for you to review.
The [JAP PRD](https://claude.ai/artifact/WL9RYjsPDCTmknMLRqzKRj) is the single source of truth;
the [build plan](https://claude.ai/artifact/1TrqC2jt8SuPVbULa8i6eU) and the
[engineering design doc](https://claude.ai/artifact/XsWXaEvBdEsFf97GguqtAy) follow it.

## What runs

| Service | URL (127.0.0.1 only) | What it is |
| --- | --- | --- |
| ui | http://localhost:5173 | React + Vite UI: My profile, and a Dashboard with jobs, possible duplicates and the status checks |
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

## One row per job

The same job is often posted on more than one board. After each run JAP groups postings into
listings: one row per company and role, with a badge for each board. Company names are
cleaned (Inc, Ltd, Technologies and the like are dropped, and aliases in `company_alias` map
to one name) and titles are reduced to a canonical role (Sr becomes Senior, locations and
work-mode words are dropped). Seniority always stays in the role, so Senior and Junior are
two rows. Postings join a listing when they share a requisition ID, have the same canonical
role, or are a near match (titles 90% alike and descriptions 92% alike). Pairs just below
that bar show under **Possible duplicates** on the Dashboard for you to Merge or Keep apart.
Merge and Split in the job panel are remembered on later runs.

## Which jobs are listed

The Dashboard lists only the jobs that match My profile. The match rule is one or more rows
of Role, Skills and Location: a job is listed when it meets every condition in at least one
row (the default lists a job that meets any one). Role means the job's cleaned title contains
one of your target roles (engineer and developer count alike); Skills means every must-have
skill is in the job; Location means a job location names one of yours, or the job is remote
and allows one of your Remote regions, or it is Remote - Worldwide. On top of that, the work
mode must be one you ticked (a job that doesn't say is listed and tagged Work mode unclear),
and excluded companies and title words are dropped.

The filter is one SQL query over the stored listings (`backend/pipeline/matching.py`), so
saving My profile updates the list in well under a second without fetching anything, and My
profile shows how many listings your unsaved changes would match. Each row shows which
conditions it matched. Totals and Jobs by role count each listing once (under the first
target role it matches, or Other); the filter bar narrows the list by role, location, status
and matched condition without changing your profile. Every run ends with the same filter.

`make demo` loads "JAP Demo Co" with six made-up postings on a Greenhouse and a Lever board,
so you can see grouping, a possible duplicate, Merge and Split without waiting for real
boards. It also loads "Globex Demo" on an Ashby board with jobs for checking the filters:
Remote - Worldwide, Remote - US, Remote - APAC, an Intern title, an unclear work mode and a
job in Berlin. Runs skip demo boards; `make demo-clear` removes them.

## What starts a run

Four things start a run, and only one run is active at a time. A trigger that fires during a
run queues one run behind it; more triggers join that queued run instead of adding another
(a role-scoped ask widens the queued run's scope).

- **Run dialog** (Run… on the Dashboard): every job on the companies you watch, or only some
  of your target roles (only those roles' jobs are stored, updated or closed), and Find only
  or Find and apply (see Applying).
- **Daily run**: the switch on the Runs card, at 06:00 in `JAP_TZ` unless you change the time
  next to it. An Inngest cron ticks every minute and fires once per day in the first five
  minutes after that time, so a run missed while the Mac sleeps is skipped, not caught up.
- **Profile save**: the stored listings are re-filtered at once, then a fetch follows in the
  background; new matching jobs appear on the Dashboard without a reload.
- **Resume update**: accepting the skills from a new resume re-filters and starts a run.

The Runs card shows the last run line (when, what triggered it, and its counts), plus the run
going now and the one queued behind it.

## Applying (Review mode)

Open a job and press **Apply now**, or pick **Find and apply** in the Run dialog to prepare
every Not applied job (up to your daily cap, 20 by default). JAP freezes the job description,
opens the posting's public form in a headless Chromium (Playwright), lists every field and
whether it is required, and fills it:

- Contact fields (name, first and last name, email, phone, LinkedIn, GitHub, website,
  location) come from My profile, by code. No model ever sees or fills them.
- The resume field gets your active resume, under its original file name.
- Other questions are looked up in the answer bank (exact question, or 80% alike).
- A required free-text question with no saved answer gets an answer drafted for this job
  from the JD and your resume (the `answer` prompt, redacted like every model call). Drafts
  are not saved to the answer bank unless you save them.
- A question only you can answer (notice period, salary, start date, work authorisation,
  relocation and the like), a required choice with no saved answer, a file JAP doesn't have,
  or a CAPTCHA stops the job at **Needs input**. Answer in the panel; the answers go to the
  answer bank (untick the box to skip that) and JAP prepares the form again.

In Review mode (the default) the job then waits at **Ready for review**:
every answer is shown, with where it came from, and editable. Nothing is sent until you
press **Approve and submit**. JAP then opens the form fresh, fills it, presses Submit once,
and keeps a screenshot of the confirmation and a Playwright trace in `data/`. The job
becomes **Applied**, and its panel shows what was sent: the date, the resume version, every
answer, the screenshot and the frozen JD. One email, "Applied: Role at Company", goes to the
address on My profile through Gmail SMTP (`GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` in
`.env`), with a link to the posting and to the job in JAP.

Submissions are at least 30 seconds apart and stop at the daily cap until midnight in
`JAP_TZ`. Submit is never pressed twice: if a retry finds it was pressed without a
confirmation, the job becomes **Failed** with "Submit outcome unknown" so you check by hand.
An Applied job has no Apply now, and a run never prepares it again, even when the employer
reposts it (a repost joins the same listing). **Skip** cancels a prepared or waiting job;
**Undo** brings it back. A **Failed** job shows the reason and screenshot, with **Retry**.

## Automatic mode

The **Review | Automatic** switch on the Runs card sets the Apply mode (A4). In Automatic mode
a job is submitted without asking you once its form is filled, unless something about it is
unclear (A3):

- a job whose posting doesn't say remote, hybrid or onsite (**Work mode unclear**) stops at
  Ready for review, with "Automatic mode left this one for you" and the reason;
- a question only you can answer, a required choice with no saved answer, a CAPTCHA or a
  failed cover letter still stops at **Needs input**;
- a job already waiting in Ready for review keeps waiting: the mode is fixed when a job is
  prepared, so switching takes effect from the next run.

Submissions are at most the **Daily cap** a day (next to the switch, 20 by default; set it to 2
to try Automatic mode) and at least 30 seconds apart. A run at the cap prepares nothing and
says "daily cap reached". The Daily run finds jobs only in Review mode, and finds and applies
in Automatic mode. Switching back to Review makes the next run prepare without submitting.

## When something fails

- **A job fails** (the form page is gone, the form changed, Submit gave no confirmation): after
  Inngest's retries it shows **Failed** with the reason, a screenshot of the page as JAP saw it
  (also when the form couldn't even be read), and **Retry**.
- **Alert emails** go to the address on My profile, once each: when a run fails, when a board
  in a run can't be fetched, when an application ends Failed (with the screenshot attached),
  and when the weekly form test can't read a form. They never carry answers or letters.
- **Spend cap**: My profile has an **AI spend** card with this month's spend and the monthly cap
  ($2, or `JAP_MONTHLY_COST_CAP_USD`). Once it is reached, model steps stop: job descriptions
  aren't read, answers aren't drafted, and no cover letter is written, so a job that needs
  one stops at Needs input ("Spend cap reached ...") and nothing is sent without it. The Runs
  card and the last run line say so. Lower the cap to $0.05 to try it, then set it back.
- **Daily backup**: an hourly Inngest cron (`daily-backup`) writes
  `data/backups/jap-YYYYmmdd-HHMMSS.sql.gz` with `pg_dump` the first time each day the Mac is
  awake, and keeps 14 days. **Back up now** on the Status card makes one at once;
  `make restore FILE=...` brings one back.
- **Weekly form test**: every Monday at 07:00 (`form-check`), JAP opens the newest open job's
  form on each ATS you watch and reads it, submitting nothing. The Status card shows the
  result per ATS; a failure sends an alert. **Test forms now** or `make form-check` runs it
  at once.
- **Dedup QA set**: `backend/tests/qa/dedup_pairs.yaml` holds 106 labelled job pairs (52 the
  same job, 54 different). `make qa` places each pair with the real dedup and prints the
  accuracy; CI fails below 95% or on any wrong merge. It found one: a bracket such as
  "(Python)" vs "(Java)" was dropped from the role, so the two jobs merged. Brackets now drop
  only places, work modes, requisition numbers and words like "full-time".

## Cover letters

JAP writes a cover letter only when the application form makes its cover letter field
mandatory. An optional cover letter field stays empty, and a JD that asks for a letter on a
form with no letter field gets none. The letter is written right before the job reaches
Ready for review, as its own step (`cover-letter` in apply-listing):

- It is written from the frozen JD, your active resume and the **Cover letter tone** note on
  My profile, names the company and role, links two or three of your real skills or projects
  to what the JD asks for, and stays under 250 words.
- A second call lists every claim the letter makes about you and quotes your resume for each.
  Code keeps a claim only if the quote really is in the resume, and also checks the length,
  the company and role, and that every number in the letter is in the resume. Any problem
  rewrites the letter once with the problems listed; if the second letter fails too, the job
  stops at **Needs input**, where you can paste your own letter or Skip. Nothing is sent
  without a required letter.
- Like every model call, the prompt is redacted: the model sees `[NAME]`, `[EMAIL]` and
  `[PHONE]`, signs off as `[NAME]`, and your name is filled in locally. The Inngest UI shows
  the start of the redacted prompt in the `cover-letter` step's output, never the letter.
- The letter goes in as the field takes it: pasted as text, or uploaded as a PDF built from a
  plain template (your name and contact line, the date, role and company, the letter) and
  named like `Your-Name-Cover-Letter-Company.pdf`. The PDF is kept in `data/cover_letters/`.

In Ready for review the letter is editable, with a word count, the claim check and the PDF.
Your edited version is what is sent and what the confirmation email attaches; the Applied
panel shows the letter and the PDF as sent.

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
| `make backup` / `make restore FILE=...` | Dump to / restore from `data/backups/` (the app also makes one every day) |
| `make qa` | Dedup QA set: place the 106 labelled job pairs and print the accuracy |
| `make form-check` | Read one live form per ATS now (the weekly form test); nothing is submitted |
| `make llm-check` | One tiny test call to each OpenRouter model (no personal data) |
| `make ollama-check` | Is Ollama answering with `nomic-embed-text` pulled |
| `make demo` / `make demo-clear` | Load or remove the demo postings used to check dedup and the filters |

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
| 0.9.0 | 29 Oct 2026 | Phase 8: Automatic mode and hardening. Review/Automatic switch and Daily cap on the Runs card, jobs with an unclear work mode held at Ready for review with the reason (A3), Daily run finds and applies in Automatic mode, "daily cap reached" and "spend cap reached" on the last run line, spend cap set on My profile (AI spend card), screenshot kept when a form can't be read, alert emails for failed runs, boards, applications and form tests (notify-failure), daily pg_dump backup (daily-backup, 14 days kept), weekly live form test per ATS (form-check), Backups and form tests on the Status card, dedup QA set of 106 labelled pairs (`make qa`) and the bracket fix it led to, `make form-check`, `make help` fixed when `.env` exists |
| 0.8.0 | 29 Oct 2026 | Phase 7: cover letters. Written only for a mandatory cover letter field (`cover_letter` prompt, as its own `cover-letter` step), claim check against the resume (`claim_check` prompt plus code checks for quotes, length, company and role, numbers), one rewrite, Needs input after two failures with your own letter accepted, text or PDF delivery, editable letter in Ready for review, letter and PDF in What was sent and attached to the email |
| 0.7.1 | 29 Oct 2026 | Match rule fix: the rule in words, a warning for rows that can't narrow the list, one-click rules (Any one, Role and Location, Role and Skills, All three), per-condition counts in the preview, and locations typed with their region ("Bangalore, India") match by city |
| 0.7.0 | 29 Oct 2026 | Phase 6: Apply in Review mode. Apply now and Skip in the job panel, apply-listing (JD snapshot, Playwright form reader, contact fields from My profile by code, resume upload, answer bank, drafted answers for required free text), Needs input with answers saved to the answer bank, Ready for review with every answer editable, Approve and submit (daily cap, 30 s gap, Submit pressed at most once), confirmation screenshot and trace, What was sent with the frozen JD, "Applied: Role at Company" email, Find and apply in the Run dialog, apply counts on the last run line, Retry for Failed; README history row for 0.6.0 moved here from the services table |
| 0.6.0 | 29 Oct 2026 | Phase 5: Run dialog with role scope, Daily run switch and time (minute cron in `JAP_TZ`), background run after a profile save, run after accepting resume skills, one run at a time with repeats merged into the queued run, Runs card with last run line, run in progress and queued run |
| 0.5.0 | 29 Oct 2026 | Phase 4: match rule as one SQL query (Role, Skills, Location, Remote regions, work mode, exclusions), re-filter on profile save and after every run, match preview on My profile, Totals and Jobs by role, filter bar (role, location, status, matched condition), matched-condition tags, Globex Demo jobs in `make demo` |
| 0.4.0 | 29 Oct 2026 | Phase 3: canonical company and role, dedup into listings (requisition ID, exact and near match), Possible duplicates with Merge and Keep apart, Merge and Split in the job panel remembered across runs, merged count on the last run line, `make demo` |
| 0.3.0 | 29 Oct 2026 | Phase 2: Greenhouse, Lever and Ashby adapters, board detection from a URL or name, Find only runs (run-pipeline, fetch-board, enrich-posting), extraction cached by description hash, Closed postings, job list with the JD side panel |
| 0.2.2 | 29 Oct 2026 | My profile: "No unsaved changes" next to a disabled Save, an inline hint when no work mode is ticked, and work modes compared as a set |
| 0.2.1 | 29 Oct 2026 | Writer model is `anthropic/claude-sonnet-4.5` (normal calls); OpenRouter only takes `:batch` ids through its batch service |
| 0.2.0 | 29 Oct 2026 | Phase 1: full schema (0001_initial), My profile, resume upload and parsing, redact module, model spend log and $2 cap, Companies to watch, answer bank |
| 0.1.2 | 29 Oct 2026 | Inngest host port 8290, since another Inngest dev server also takes 8289 |
| 0.1.1 | 29 Oct 2026 | Host ports set in `.env`; API on 8001 and Inngest on 8289 by default |
| 0.1.0 | 29 Oct 2026 | Phase 0: repo layout, Docker Compose (ui, app, inngest, db, adminer), Makefile, FastAPI health and Inngest endpoint, status page, CI |
