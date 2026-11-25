# JAP: Job Application Pipeline

**Version 0.32.0 (The AI reads and writes less: my roles only, no repeat of a job it left out, a checker only for free text), 25 Nov 2026.**

JAP watches the public job boards of the companies you pick (Greenhouse, Lever and Ashby),
lists the jobs that match your profile, and prepares or submits applications for you to review.
It is a personal, single-user tool today: one account, one profile, one resume (see
[Sign-in and accounts](#sign-in-and-accounts)).
The JAP PRD is the single source of truth. Since 23 Nov 2026 it is kept as Markdown in `docs/`,
which is not in git because it carries personal data (see `AGENTS.md`); the
[claude.ai copy](https://claude.ai/artifact/WL9RYjsPDCTmknMLRqzKRj) this README links to is
where it came from and is no longer the maintained one. The
[engineering design doc](https://claude.ai/artifact/XsWXaEvBdEsFf97GguqtAy) follows it. The
[build plan](https://claude.ai/artifact/1TrqC2jt8SuPVbULa8i6eU) was retired on 23 Nov 2026:
all of its phases (0 to 8) are built.

## What runs

| Service | URL (127.0.0.1 only) | What it is |
| --- | --- | --- |
| ui | http://localhost:5173 | React + Vite UI: Jobs, Profile, Job preferences and Settings |
| app | http://localhost:8001 | FastAPI JSON API and Inngest functions (`/api/health`, `/api/inngest`) |
| inngest | http://localhost:8290 | Inngest dev server, runs every pipeline step |
| db | localhost:5432 | Postgres 16 with pgvector, pg_trgm, pgcrypto |
| adminer | http://localhost:8080 | Database browser (server `db`, user/password/db `jap`) |

Each host port can be changed in `.env` (`JAP_UI_PORT`, `JAP_API_PORT`, `JAP_INNGEST_PORT`,
`JAP_DB_PORT`, `JAP_ADMINER_PORT`). The API and Inngest use 8001 and 8290 so they don't clash with
other projects on 8000 and 8288-8289 (an Inngest dev server publishes both). Inside Docker the services keep their usual ports.

Ollama is **not** in Docker. It runs natively on the machine that runs Docker (on a Mac it can
use the GPU), and the app reaches it at `http://host.docker.internal:11434`.

Today that machine is a VM, not the Mac: both stacks run there, and an ssh port forward
brings these addresses to the Mac, so they open as written. See
[Hosting](#hosting-local-and-live).

## The screens

The UI follows the [redesign mockup v0.5](https://claude.ai/artifact/5V2SRvBgWZu5stN5KY4H7L)
(PRD 1.5, U1 to U20): a sidebar with five sections, which becomes a top bar on a phone.

- **Jobs**: the last search line, **Find new jobs**, a status strip (All open, New, Needs you,
  Ready to send, Sent, Failed), a toolbar with search and filters, and the jobs table. Tick
  rows to **Apply** or **Hide** several at once; click a row to open the job panel, whose
  actions and content follow the job's status. `/` jumps to the search box (its tooltip says so).
- **Profile**: what is sent to employers. Resume and its versions, contact details and links,
  Saved answers, and the cover letter style.
- **Job preferences**: what decides which jobs show. Job titles, locations, companies to
  follow, jobs to hide, and Matching; and your required and nice-to-have skills, which never
  hide a job but give each one a skills match.
- **Insights**: jobs found and sent per week, results by job site, and AI spend (PRD 1.19).
- **Settings**: applying (JAP prepares, I send; or JAP applies for me, with a daily limit
  and a required-skills bar),
  the daily search, Search history, AI usage, System status (backup and form check) and
  Health (Daily search, stuck searches, errors, slow pages, disk, alerts sent).

Older names in this README map to the screen as follows: Not applied is **New**, Needs input
is **Needs you**, Ready for review is **Ready to send**, Approve and submit is **Send
application**, Applied is **Sent**, Skip is **Hide**, Retry is **Try again**, the answer bank
is **Saved answers**, a run is a **search**, the spend cap is the **AI limit**, My profile is
split into Profile and Job preferences, and the Runs and Status cards are in Settings.

## First run (macOS)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and [Ollama](https://ollama.com/download).
2. Pull the embedder: `ollama pull nomic-embed-text`
3. `make start` creates `.env` from `.env.example` the first time. Put your OpenRouter key and
   email sender in `.env` (it is gitignored; never commit it). See [Sending email](#sending-email).
4. Open http://localhost:5173 and create your account. The first account is the admin
   (see [Sign-in and accounts](#sign-in-and-accounts)). Settings > System status should show
   API, Database, Inngest and Ollama as OK.

## Sign-in and accounts

Every page and every `/api` route is behind sign-in (PRD 1.18, S1 to S9); only `/api/health`
and `/api/auth/*` answer without a session.

**One account, one person.** JAP today holds a single person's data: one profile, one active
resume, one answer bank, one set of jobs. No table outside `app_user`, `auth_session` and
`password_reset` carries a user id, so a second account would read and change all of it, and
would apply to jobs under your name and with your resume. Sign-up is therefore closed after
the first account and there are no invites. Several people each with their own data is PRD
1.18 step 2 (S7 in full, then S10 to S13), and it needs a user id on every table first.

- **First account.** On a fresh JAP the sign-in screen offers Create your JAP account. That
  account is the admin, and sign-up then closes: `POST /api/auth/sign-up` answers 409
  `sign_up_closed` from then on. Set `JAP_ADMIN_EMAIL` in `.env` to allow only your address.
- **Passwords** are at least 12 characters and stored as salted scrypt hashes. Five wrong
  tries lock that account's sign-in for 15 minutes.
- **Forgot password** emails a single-use link, good for 30 minutes, from the mailbox in
  `.env` to the account's address. It links to `JAP_UI_URL`, so set that to the address
  you open JAP at. Without a mailbox, use `make set-password EMAIL=...`.
- **Two-step sign-in** (Settings > Account): scan the QR code with an authenticator app,
  enter one code, and save the 8 recovery codes. Lost both? `make two-step-off EMAIL=...`.
- **Sessions** live in an HttpOnly cookie tied to the hostname. They end after 7 days unused or
  30 days after sign-in. Set `JAP_COOKIE_SECURE=true` wherever JAP is served over HTTPS.
- **Internal endpoints.** `/api/inngest` answers only direct calls from inside Docker, never
  a request carrying proxy headers (`X-Forwarded-For`, `Forwarded`, `Cf-Connecting-Ip`), and
  a write that comes from another site's page is refused.

## Personal data and models

No name, email, phone, address or profile link is sent to any model. `backend/llm/redact.py`
swaps them for placeholders such as `[NAME]`, a guard blocks any request that still contains
one, and `backend/llm/client.py` is the only code that talks to OpenRouter (a test enforces
it). Each call is logged in `llm_call` with its cost; model steps stop once this month's
spend reaches `JAP_MONTHLY_COST_CAP_USD`. When a trace service is set, numbers about AI work
(times, token counts, counts of items, ids) go to it and no text about you or about a job
(`backend/observability/trace.py`, the only code that talks to it; a test enforces both).

## Fetching jobs

Add companies under **Companies to follow** in Job preferences by pasting their Greenhouse,
Lever or Ashby board URL (or typing the name), then press **Find new jobs** on Jobs. Each board is one `fetch-board` run
in Inngest, throttled to one request a second per ATS. Job descriptions are read once per
description, so a re-run with unchanged postings costs nothing. From 0.20.0 the reading is
done by the scheduled AI reader (below) by default; `JAP_READER=api` in `.env`
brings back the extract model, where the first run on a big board (some list 700+ jobs)
makes one model call per job, so start with a few companies and watch **AI usage** in
Settings.

**Stop following** a company (the × on its name) asks first and says how many jobs it hides.
It closes every open posting of that board with the reason `unfollowed` and closes the jobs
left with no open posting on a company you still follow; nothing is deleted. Jobs you applied
to, or started an application for, stay in the list, and their panel says you stopped
following the company. No job description of an unfollowed company is read by the AI, reads
already queued included, and a search that is running stores nothing for it.

**Following it again** switches the same board back on and fetches nothing until the next
search. That search reopens the jobs the board still lists, on the same rows: an unchanged
description reuses its stored reading, a changed one is read once, a job the employer took
down meanwhile stays Closed (its reason becomes `gone_from_board`), and a job you applied to
stays Sent. The search line counts reopened jobs apart from new ones.

Companies unfollowed before 0.17.0 left their postings open. `make close-unfollowed` lists
them and changes nothing; `make close-unfollowed APPLY=1` closes them. On live, run
`make backup ENV=live` first.

## One row per job

The same job is often posted on more than one board. After each run JAP groups postings into
listings: one row per company and role, with a badge for each board. Company names are
cleaned (Inc, Ltd, Technologies and the like are dropped, and aliases in `company_alias` map
to one name) and titles are reduced to a canonical role (Sr becomes Senior, locations and
work-mode words are dropped). Seniority always stays in the role, so Senior and Junior are
two rows. Postings join a listing when they share a requisition ID, have the same canonical
role, or are a near match (titles 90% alike and descriptions 92% alike). Pairs just below
that bar (titles 75%, descriptions 85%) are settled by the AI after each search's dedup: the
extract model reads both descriptions once and JAP merges them or keeps them apart, so you are
never asked. A pair it can't answer stays as two jobs until the next search. **Not the same
job** in the job panel splits a wrong join, and is remembered on later searches.

## Which jobs are listed

Jobs lists only the jobs that fit your Job preferences. The match rule is one or more rows
of Role and Location: a job is listed when it meets every condition in at least one
row. Job preferences offers two choices under **Matching**: job title or location; job
title and location (the default, in the database and in code).
A rule saved earlier that is none of these is kept and shown as Custom. Role means the job's cleaned title contains
one of your target roles (engineer and developer count alike); Location means a job location names one of yours, or the job is remote
and allows one of your Remote regions, or it is Remote - Worldwide, or you chose **All remote
jobs** (`remote_any`). Whether a job is remote, and its regions, come first from what the ATS
states (Ashby and Lever workplace type, Ashby isRemote, a location entry such as "Remote -
India" or "Remote" beside "India"; `backend/sources/workplace.py`), so this works before or
without the model reading the job, and the model fills in the rest. Under Locations, office cities come with On-site and Hybrid ticks, and
remote jobs are one of three choices: none, only those open to your countries, or all. On top of that, the work
mode must be one you ticked (a job that doesn't say is listed with Work mode "Not stated"),
and excluded companies and title words are dropped.

The filter is one SQL query over the stored listings (`backend/pipeline/matching.py`), so
saving Job preferences updates the list in well under a second without fetching anything,
and the save bar shows how many jobs your unsaved changes would fit. Each row's "Fits" line shows
what it matched. The status strip counts each job once; the Job title filter counts each
job under the first job title it matches, or Other. The toolbar searches title, company and
location and filters by job title, location, work mode, posted date and what it fits on,
without changing your preferences. Every column sorts, and the table pages 10, 25 or 50 rows. Every run ends with the same filter.

### Skills match

Skills never hide a job (PRD 1.21, F5). Until 0.18.0 a third Matching choice listed a job
only when it named every one of your required skills, and a job whose description gave no
skills failed it, so good jobs went missing. Now the same SQL pass that filters also writes
a skills match on every listing (`required_hit`, `nice_hit`, `required_pct`), and Jobs
shows it in its own column:

- **Required**: how many of your required skills the job asks for, with the percentage and
  the missing ones, for example "4 of 6 required (67%)", "Missing aws, kafka".
- **Nice to have**: how many of your nice-to-have skills it asks for, "2 of 5 nice to have".
  With no required skills set, only this part shows.
- **Skills not stated**: no skills were found in the job description. The match is unknown,
  not 0, like Work mode "Not stated".

The Skills match column sorts (required %, then nice to have, unknown last), and the toolbar
filters by a minimum (25%, 50%, 75% or all of your required skills; unknown jobs are left
out while a minimum is set). Neither changes Job preferences. The job panel's **Why it fits**
lists the skills behind the numbers. The match is rewritten whenever you save your skills,
accept the skills from a resume, or JAP finishes reading a job description.

A match rule saved before 0.18.0 that used skills keeps its other conditions: a row of skills
alone is dropped, and a rule left empty becomes job title and location (migration
`0010_skills_match`, and the same when a rule is read).

`make demo` loads "JAP Demo Co" with six made-up postings on a Greenhouse and a Lever board,
so you can see grouping, a pair for the AI to settle, and Not the same job without waiting for real
boards. It also loads "Globex Demo" on an Ashby board with jobs for checking the filters:
Remote - Worldwide, Remote - US, Remote - APAC, an Intern title, an unclear work mode and a
job in Berlin. Runs skip demo boards; `make demo-clear` removes them.

## What starts a run

Four things start a run, and only one run is active at a time. A trigger that fires during a
run queues one run behind it; more triggers join that queued run instead of adding another
(a role-scoped ask widens the queued run's scope).

- **Find new jobs** on Jobs: every company you follow. The arrow beside it searches for only
  some job titles (only those jobs are stored, updated or closed) and can also apply to the
  new jobs that fit (see Applying).
- **Daily search**: the switch in Settings, at 06:00 in `JAP_TZ` unless you change the time
  next to it. An Inngest cron ticks every minute and fires once per day in the first five
  minutes after that time, so a run missed while JAP is down is skipped, not caught up.
- **Job preferences saved**: the stored listings are re-filtered at once, then a fetch follows
  in the background; new matching jobs appear on Jobs without a reload. Saving only contact
  details or the cover letter style on Profile starts no search.
- **Resume update**: accepting the skills from a new resume re-filters and starts a run.

The line under the Jobs title shows the last search (when, what started it, and its counts)
or the one going now; **Search history** in Settings lists every search with its problems.
Its colour follows the search: teal with a pulsing dot while searching (at least 3 seconds;
the page checks every 2 seconds), green with **Show new** after a search that found new jobs
(until you next open Jobs), grey when nothing is new, orange when a company could not be read
or the AI limit was reached, and red with **Try again** when the search failed.

## Applying (Review mode)

Open a job and press **Apply** (or tick several rows and press Apply), or tick **Also apply to
the new jobs that fit** under the arrow beside Find new jobs to prepare every New job (up to your daily cap, 20 by default). JAP freezes the job description,
opens the posting's public form in a headless Chromium (Playwright), lists every field and
whether it is required, and fills it:

- Contact fields (name, first and last name, email, phone, LinkedIn, GitHub, website,
  location) come from My profile, by code. No model ever sees or fills them. A phone field
  on a form with no country code picker of its own (Ashby) gets the number with its country
  code (+91 when you live in India and the saved number has none). A location typeahead
  (Ashby's Location) is typed into and the closest place picked.
- Every Ashby field type is read: text, Yes/No and multiple-choice radios, Yes/No buttons
  (each answer clicked under its own question), the location typeahead and checkboxes, all
  marked required from the question's title as Ashby draws it.
- The resume field gets your active resume, under its original file name.
- Other questions are looked up in the answer bank (exact question, or 80% alike).
- A required free-text question with no saved answer gets an answer drafted for this job
  from the JD and your resume (the `answer` prompt, redacted like every model call). Drafts
  are not saved to the answer bank unless you save them.
- A question only you can answer (notice period, salary, start date, work authorisation,
  relocation and the like), a required choice with no saved answer, or a file JAP doesn't
  have stops the job at **Needs input**. Answer in the panel; the answers go to the
  answer bank (untick the box to skip that) and JAP prepares the form again.
- A picture puzzle (CAPTCHA) is never solved or worked around. JAP fills in everything else,
  stops before Submit and the job shows **Finish yourself: puzzle** (see below).

Just before pressing Submit, JAP reads the form again as the page holds it. A required field
still empty, a choice showing another answer than JAP's, or a CAPTCHA that has appeared stops
it before Submit with the reason, so nothing half-filled is sent.

**Finish yourself: puzzle.** The job panel says the form has a puzzle and nothing was sent.
On the Mac, in the JAP folder, run `make finish ENV=live` (or `make finish APP=<id> ENV=live`,
copied from the panel). It opens the same form in a visible Chromium window, filled in with
your answers and resume by the same code JAP submits with. Solve the puzzle and press Submit
there; JAP waits up to 20 minutes for the page to confirm, keeps a screenshot, marks the job
**Applied** and sends the usual email. If you send it another way, press **I sent it** in the
panel. The first `make finish` installs Chromium for it (needs `uv`). A puzzle that only shows
after Submit holds the form, so it counts as not sent and becomes Finish yourself too.

In Review mode (the default) the job then waits at **Ready for review**:
every answer is shown, with where it came from, and editable. Nothing is sent until you
press **Approve and submit**. JAP then opens the form fresh, fills it, presses Submit once,
and keeps a screenshot of the confirmation and a Playwright trace in `data/`. The job
becomes **Applied**, and its panel shows what was sent: the date, the resume version, every
answer, the screenshot and the frozen JD. One email, "Applied: Role at Company", goes to the
address on My profile through the configured sender (see [Sending email](#sending-email)),
with a link to the posting and to the job in JAP.

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
- so does a job the AI writer's checker flagged ("Checker: ..."), and a form or job
  description that bans AI-written answers (V6, PRD 1.29);
- a question only you can answer, a required choice with no saved answer or a failed cover
  letter still stops at **Needs input**;
- a form with a picture puzzle stops at **Finish yourself: puzzle**, filled in, for you to
  finish with `make finish`;
- a job already waiting in Ready for review keeps waiting: the mode is fixed when a job is
  prepared, so switching takes effect from the next run.

A run in Automatic mode also picks only jobs at or above the **required-skills bar** (F6):
"Only jobs with at least N% of my required skills" on Settings, 60 by default. A job below
the bar, or with Skills not stated, is not prepared: it stays Not applied on Jobs for you to
apply to yourself, and its panel says so under Why it fits. With no required skills in Job
preferences, or the bar set to 0, there is nothing to measure and the bar is off. Review
mode has no bar, because you read every application before it goes.

Submissions are at most the **Daily cap** a day (next to the switch, 20 by default; set it to 2
to try Automatic mode) and at least 30 seconds apart. A run at the cap prepares nothing and
says "daily cap reached". The Daily run finds and prepares the new jobs that fit in both modes
(PRD 1.22, W1): Automatic mode sends them, Review mode leaves them at Ready for review or Needs
you. The scheduled AI writer (outside JAP) can then answer what is at Needs you from the
resume and answer bank; nothing is sent before you approve. Switching back to Review makes the
next run prepare without submitting.

### Automatic at volume (PRD 1.29, V1 to V8)

Automatic mode is meant to send 10 or more applications a day without you, so it paces itself
so that no job board rate-limits or flags you:

- **Random picks.** A run takes the matching jobs in random order, not newest first, so the day
  spreads across companies. Review mode keeps newest first.
- **Per company.** At most 2 attempts a day at one company (`JAP_PER_COMPANY_DAY`), counting
  every attempt started that day, sent or not.
- **Per job board.** At most 8 submits a day on one ATS (Greenhouse, Lever or Ashby,
  `JAP_PER_ATS_DAY`), at least 5 minutes apart on the same ATS (`JAP_ATS_GAP_SECONDS`), on top
  of the 30 seconds between any two submits plus 0 to 30 seconds that differ per job
  (`JAP_APPLY_JITTER_SECONDS`).
- **A company's own limit.** Some employers cap applications from one person and say so on
  the form (Supabase: "up to 3 applications within a 60-day period"). Past it the form takes
  the submit and drops it silently. JAP reads the cap off the form and keeps it on the company
  (`company.apply_limit`, `apply_limit_days`); once that many have been sent in the window it
  fails the attempt with "Company limit: ..., nothing was sent", checks again just before
  Submit, and stops picking that company until the window has room, in either mode (JAP-13).
  Job preferences shows each such company under Company limits (`GET /api/boards/limits`):
  the limit, every job sent inside the window with its date (unconfirmed ones marked), and the
  exact date and time it opens again (PRD 1.31, V10).
- **Unconfirmed submits count.** The gaps above run from the last submit that was pressed,
  confirmed or not, since an unconfirmed one may well have reached the board.
- **Backoff.** A 429 or 403 from an ATS, or a page saying too many requests, puts that ATS on
  hold for 1 hour, then 4, then 24 on repeats (table `ats_hold`). Nothing is read or submitted
  there until the hold ends; the job waits instead of failing, and the Runs history says
  "On hold after a rate limit". A confirmed submit there clears the strikes.
- **The AI writer answers Automatic jobs too.** `writer-export` now includes Automatic jobs
  at Needs you, with a `review_rule`: a job its checker flags comes back with
  `"_review": "<reason>"`, which is kept as the application's `hold_reason`, so it stops at
  Ready for review and is never sent on its own. A writer that leaves a job out also leaves it
  unsent.
- **Extra picks.** A run picks up to the daily cap plus 5 (`JAP_AUTO_EXTRA_PICKS`), less the
  Automatic attempts still in flight, so jobs held for review or a puzzle don't leave the day
  short. Submits still stop at the cap; the rest wait for the next day's cap.

## AI reader

PRD 1.25, R1 to R7 (the Claude reader until 1.30). With `JAP_READER=agent` (the default;
`claude` still works) JAP sends no job description to OpenRouter. It still copies what the ATS
states (work mode, remote regions, Ashby salary) and makes the dedup embedding, and the rest
(skills, seniority, salary, visa sponsorship, remote regions, cover letter asked) waits for the
AI reader on your own AI plan, started by the Mac's schedule after the Daily run and before the
AI writer (see [AI harness](#ai-harness)). Until it
runs, a new job shows Skills not stated and, unless the ATS says, Work mode unclear, so
Automatic mode does not send it.

- `make ENV=live reader-export` prints one batch (`LIMIT=50` by default) of unread postings
  as JSON: open, company followed, description changed since it was last read. Postings
  whose title is one of your target roles come first, so the jobs that can be listed for you
  are read in the first minutes of a big backlog. `SHARD=3/8` takes only the third of eight
  disjoint shares, so eight readers can work at once without overlap. Each has its
  id, description hash, title, locations, description (cut at 12,000 characters) and the
  keys still wanted, with the extract prompt's rules and the field schema. Descriptions are
  redacted like any model call, the export is refused if anything personal is left, and
  nothing about you (resume, profile, answers) is in it. `run_active` says a search is
  still running; `unread_total`, `unread_in_my_roles` and `unread_in_shard` how many are
  left.
- `make ENV=live reader-import FILE=fields.json` takes `{"<posting id>": {"description_hash":
  "...", "skills": [...], ...}}` and checks each posting against the extraction schema:
  every wanted key, nothing else, only allowed values. A posting that fails, or whose
  description changed since the export, is not saved and stays unread; one that passes is
  saved as if JAP had read it, and its job's skills, work mode and match are refreshed.
  The last line says `saved N of M`. Nothing is applied or sent.
- Possible duplicates (PRD 1.28, R8): with `JAP_READER=agent` JAP sends no pair to
  OpenRouter. Shard 1's export also carries `pairs` (both jobs' title, locations and
  redacted description) with `pair_instructions`, the rules of JAP's same-job prompt;
  answer each under `"pair:<pair id>": {"same": true or false, "reason": "..."}` in the same
  import. Same merges into the older job, not same keeps them apart, as before.

**Only my roles are read** (PRD 1.37, R3, from v0.32.0). When every combination of your match
rule needs a role (the default, job title and location), a posting whose title is not one of
your target roles can never be listed, so the reader is not given it: it stays unread and is
read once your roles take it in. On 20 Nov 2026 that was 239 of 1,531 unread postings.
`JAP_READER_SCOPE=all` reads every posting, your roles first, as before. `make reader-status`
says `unread` (to be read) and `unread_all` (unread in all); the export says
`unread_to_read` and `scope`.

The runner starts 8 workers, one per shard (`JAP_AGENT_SHARDS`), and asks for 10 postings a
batch (`JAP_AGENT_BATCH`), not the 50 a `make reader-export` by hand gives; each worker
repeats export, read, import until its batch is empty or saves nothing, and one more pass
then takes whatever is left. The first read of a large backlog (thousands of postings)
is a one-off and can reach your plan's usage limit; what is left is read on the next run.
After that only new or changed postings are read. `make reader-status` prints one line
(`run_active=0 unread=12 pairs=1`) for the runner.
`JAP_READER=api` (was `openrouter`) is the fallback: JAP reads each posting itself again,
with a funded key.

## AI writer

PRD 1.22, W1 to W7, and 1.27, W10 (the Claude writer until 1.30). With `JAP_WRITER=agent` (the
default; `claude` still works) JAP never asks OpenRouter to draft a form answer: a free-text
question with no saved answer stops the job at **Needs you** ("Waiting for the AI writer"),
and the AI writer, started by the Mac's schedule after the reader, answers it without ever
reading the database, and a second session checks every answer before it goes back (W8).
`JAP_WRITER=api` (was `openrouter`) brings back JAP's own drafting as the fallback.
From 0.22.0 (PRD 1.28, W11 and W12) it also writes a required cover letter (the question is
marked `cover_letter` and follows `cover_letter_rules`; JAP builds the PDF and fills the field)
and reads a new resume (each entry in `resumes`, answered under `"resume:<id>": {"skills":
[...], "titles": [...], "years_experience": 9}` following `resume_rules`; the resume shows
Reading until then). With both settings on `agent`, JAP makes no OpenRouter call.

- `make ENV=live writer-export` prints the jobs at Needs you in Review mode as JSON: each
  job's frozen description and open questions (with their options and length limits), the
  active resume and the answer bank. Name, email, phone, address and every profile link
  (also links saved in the answer bank) become `[NAME]`, `[EMAIL]`, `[PHONE]`, `[ADDRESS]`
  and `[LINK_n]`, and the export is refused if anything personal is left.
- `make ENV=live writer-import FILE=answers.json` takes `{"<application id>": {"<key>":
  "<answer>"}}`, fills the placeholders back in locally and answers each job through the
  same step as the Needs you panel, so it is prepared again and reaches Ready for review.
  Every open question of a job must be answered at once; drafts are not saved to the
  answer bank. Nothing is approved or sent.
- `make writer-status` prints one line (`jobs=3 resumes=0 waiting_on_you=1`) for the runner.
- **A job left out is not handed out again unchanged** (PRD 1.37, W13, from v0.32.0). The
  runner adds `"_handed_out"` (the application ids of the export) to what it imports; JAP
  notes each of those the answers leave out with a hash of what the writer was given (the
  resume, the answer bank, the job's open questions and description) and skips it in the
  export until one of them changes, or for 7 days at most. Its open questions then say the
  writer left it out, and it counts as `waiting_on_you`: usually it needs a fact only you
  know, so add it to the answer bank and the next run takes the job again (migration 0015).
- **A checker session only for free text** (W8, from v0.32.0): a draft whose answers are all
  chosen from options goes in without one, since the import refuses an answer that is not
  one of the options; a job the writer held stays held. `JAP_CHECKER_WHEN=always` checks
  every draft.

## AI harness

PRD 1.30, AH1 to AH9. The AI reader and writer run on whichever agent harness you pay for:
Claude Code, OpenAI Codex CLI, Gemini CLI, opencode or Devin's command line tool (from 1.33),
each on its own subscription and models. Switching is one line and one command. Everything is in `agent/`:

| Part | What it is |
| --- | --- |
| `agent/run.sh` | The runner (`make agent-run JOB=reader|writer`). It exports each batch, starts the harness once per batch, imports what comes back, runs the reader's 8 shards in parallel plus a leftover pass, runs the writer and then a checker session before anything is imported, and stops when the plan's usage limit is reached (exit 75; the rest waits for the next run). One reader and one writer run at a time. |
| `agent/jobs/` | The job prompts, one per job (`reader.md`, `writer.md`, `checker.md`), written for any harness. The harness sees only one batch folder: `in.json` from the export, the prompt, and the files it writes (`out.json`, `draft.json`, `findings.txt`, `reasons.json`). It runs no command, reads no database, needs no shell or web. |
| `agent/harnesses/` | One small adapter per harness: how to run a prompt without a chat window, how to get its record of the session, and how its usage-limit message reads. Add a harness by copying one and following `common.sh`. |
| `agent/trace.py` | The trace of each run (PRD 1.33, O16 to O21; 1.34, O24): numbers and codes only. See below. |
| `agent/contract/` | JSON Schemas of the reader and writer exports and imports, at `contract_version` 1 (in every export). |
| `agent/schedule.sh` | The Mac's schedule (launchd), replacing the Claude app's scheduled tasks. |

Settings, in `.env.live` (see `.env.example`): `JAP_HARNESS` (`claude`, `codex`, `gemini` or
`opencode`), `JAP_HARNESS_MODEL` (empty = the harness's default), `JAP_CHECKER_HARNESS` and
`JAP_CHECKER_MODEL` (the checker pass; empty = the same, `none` = no check),
`JAP_AGENT_SHARDS` (8 reader shards in parallel), `JAP_AGENT_BATCH` (10 postings per reader
batch), `JAP_AGENT_TIMEOUT_MIN` (20 minutes per harness session), `JAP_AGENT_TRACE` (0 = do
not ask the harness for a record of its session), `JAP_AGENT_SEND_S` (60 seconds JAP may
take over a run's trace before the runner keeps it for the next run), `JAP_AGENT_CALL_S`,
`JAP_AGENT_CALL_TRIES` and `JAP_AGENT_RETRY_S` (each call to JAP stopped after 120 seconds,
and one that did not reach JAP tried 3 times, 30 seconds apart) and
`JAP_AGENT_READER_AT` / `JAP_AGENT_WRITER_AT` (07:05 and 07:30, the Mac's time). The writer has no batch size: one
session takes everything that is waiting, and the checker session follows it.

**First time, moving off the Claude app's tasks.** Run `make ENV=live agent-schedule`, then
pause the two JAP tasks under Scheduled in the Claude app so nothing runs twice. `make
ENV=live agent-status` shows both jobs and their last runs. Each run's log stays in
`data-live/agent/runs/` for 30 days; its batch folders sit in the temp folder, outside the
repo, for 3 days (`JAP_AGENT_WORK_DIR` moves them). The jobs run under `caffeinate -is` so
the Mac cannot idle sleep while one is going, nor sleep at all while it is on power — it
cannot stop the lid being closed on battery — and keep any `DOCKER_HOST` the schedule was
installed with.

**Runs that finish** (PRD 1.35, from v0.30.0). Three of five mornings, 21 to 25 Nov 2026, broke
because the runner was on a Mac asleep on battery: macOS woke it for 20 seconds every 10
minutes, so a run crept on in slices, and each export hung on a dead ssh connection to the
VM for half an hour before it failed. Now:

- Every call to JAP (status, export, import) is stopped after 2 minutes, and one that did not
  reach JAP (stopped at that limit, or ssh or Docker failed) is tried again, 3 tries, 30
  seconds apart. One JAP answered, even with an error, is not, since trying again would
  change nothing. A retried import is safe: what was already saved answers "already read" or
  "not answered".
- A call that never reached JAP no longer reads `export_failed` or `import_failed`: the cause
  is `slept` when the Mac slept during it (`kern.waketime`), else `unreachable`. Those two
  now mean that JAP itself answered with an error.
- The schedule tries each job again every hour, at the same minute, until 22:00, until one
  run of it has finished that day (done, nothing to do, or stopped at the usage limit), so a
  morning lost to a sleeping Mac is made up the first hour it is awake. A scheduled run does
  not start in a dark wake (the Mac awake for a few seconds with no display, which
  `pmset -g systemstate` shows as no Graphics), and a try on a day already done does
  nothing and leaves no run log. Run `make ENV=live agent-schedule` once to install the
  hourly tries. A run by hand is not affected.
- **An email when a run breaks** (O25): when the runner hands a run's trace to JAP, JAP
  emails the address on My profile if the run failed, stopped at the usage limit, or (the
  reader) saved nothing while postings waited. One a day for each job, so a broken morning's
  hourly tries send one email; counts, causes and what to do for each, never text from the
  run. It goes with the trace, so when the Mac cannot reach JAP it goes once it can.
  `JAP_ALERTS_ENABLED=false` sends none. `docs/failures.md`, the log of failed applications and the lessons from them that every
harness keeps, is copied into every batch folder for the harness to read; it is not in git,
holds no personal data and never overrides the job prompt (`JAP_FAILURES_FILE` moves it).

**What a run leaves behind** (PRD 1.33, O16 to O21). Every run is traced, in numbers only:
no prompt, job text, resume, answer or reply is kept in a trace.

- The runner times each export, session and import and, when a session fails, names the
  cause in the run log: `not_logged_in`, `slept`, `timeout`, `usage_limit`, `no_output`,
  `export_failed`, `import_failed` or (from 1.35) `unreachable`. Before 1.33 the first, second and fifth all read
  "wrote no out.json (exit 1)".
- Where the harness gives a record of its own session, each model request (time, tokens in
  and out, cached tokens) and each tool call (which tool, which file of the batch folder,
  how long, whether it worked) is kept too. Each batch's line in the run log then ends with
  them: `saved 10 of 10 · 42 s · 4 model requests · 3 tool calls · 208,102 tokens in, 3,797
  out · claude-opus-5-5`. A harness that gives no record gets an estimate from the file
  sizes, said to be one.
- One file per run in `data-live/agent/traces/` (30 days) holds every session of the run, and
  `trace.json` in each batch folder (3 days) holds that session. `make ENV=live agent-status`
  shows the last run's sessions, time in the model, tokens and causes.
- A usage limit is read from the harness's record, or from its last words when it failed,
  no longer searched for in everything it printed: a session that worked and quoted a "sales
  quota" used to stop the run, and Claude Code's "You've hit your weekly limit" was not
  recognised at all.

**JAP's doors** (O17, level 1, from v0.28.0). Each export and each import is also counted by
JAP itself, so a harness driven by hand with no runner is covered too: one `log_event` row
(`agent.export` or `agent.import`, kept 30 days) with how many postings, pairs, jobs,
questions and resumes went out and how big the export was, how many came back, how many
were saved, and why the others were not, as codes (`missing_field`, `changed`, `gone`,
`already_read` and so on). Counts only: no posting or application id, no title, no answer.
The same goes to the trace service when one is set
([Logs, Health and Insights](#logs-health-and-insights)).

**Why a job was left out, in codes** (PRD 1.34, O24, from v0.29.0). The writer and the
checker now also write `reasons.json` beside their answers: for each job left out, held for
review or fixed, what happened (`ok`, `fixed`, `held`, `left_out`), why, from a fixed list
(`fact_not_in_bank`, `not_in_resume`, `no_option_fits`, `unclear_question`,
`unsupported_claim`, `missing_answer`, `bad_option`, `too_long`, `cover_letter_rule`,
`placeholder`, `ai_banned`, `other`), and for a fact only you know that the answer bank does
not give, which kind (`salary`, `notice_period`, `grades`, `travel`, `nationality`,
`work_permit`, `gender`, `ethnicity`, `disability`, `other`). The runner keeps a value only
when it is one of those codes, for a job or resume that was in the export, and counts what
it dropped, so no sentence gets into a trace this way. The codes are kept in the session's
and the run's trace, and the run log gets a line per job:
`why (writer): <application id>: left out (fact_not_in_bank; the answer bank lacks: salary)`.
The runner also counts the jobs left out itself, from what went in and what came back, so a
harness that writes no `reasons.json` still shows `1 job left out with no reason given`.
Before this the reason was only in the harness's last words, in a temp folder kept 3 days.
`findings.txt` stays as it is, for you to read. Each session's trace now also names the
version of the job prompt and of the contract it worked on (O20).

**The run's trace goes to the trace service** (O22, from v0.29.0). When a run's work is done
the runner hands its trace file to JAP (`make agent-trace-send FILE=...`), and JAP sends it
on when a trace service is set: one trace per run, with a span for the run, one for each
session with its export and its import, and one for each model request and tool call. The
runner itself never holds the service's key, and it takes `JAP_TRACE_ENDPOINT` and
`JAP_TRACE_HEADERS` out of a harness's environment. Handing the trace over comes after the
work, is stopped after a minute (`JAP_AGENT_SEND_S`) and never changes how a run ends. A
trace JAP could not take (the service did not answer, JAP was down, or live is still on a
version before 0.29.0) stays marked `<run>.unsent` beside its file, the run log says so,
and it is handed over again at the start of the next run of either job, once JAP has
answered: oldest first, 10 at most. A trace sent twice carries the same ids both times, so
the service can tell it is one trace (whether LangSmith then shows it once is not confirmed).
With no trace service set JAP answers `off` and nothing is kept. Tried against LangSmith
from the local stack on 24 Nov 2026 with the two stand-in runs of the tests: both were
accepted, 9 and 15 spans. A run with a real harness has not been sent yet.

Proven by a real run so far (23 Nov 2026): Devin,
with `make agent-check HARNESS=devin` (a reader batch, and a writer with its checker), and
Claude Code's reply at its plan limit. Not yet proven: the record of a Claude Code session
that worked, and Codex's, Gemini CLI's and opencode's records; `agent/README.md` keeps the
list. A record that cannot be read costs only the detail inside the session.

**Every text read whole** (PRD 1.36, AH10, from v0.31.0). JAP's export is JSON, where a long
string is one line, and Devin's tools cut a line at 2,000 characters: on 24 Nov 2026 a Devin
reader on live saved postings from the first 2,000 characters of descriptions of 6,600 to
9,000, and another searched `in.json` 108 times for single words to get round it. After each
export `agent/wrap.py` now moves every string over 600 characters (a description, the
resume, a prompt's rules) into a file of its own under `text/` in the batch folder, wrapped
at 100 characters a line, and leaves `"@text/<file>"` in its place. The job prompts (reader
1.1, writer and checker 1.4) say to read such a file to its end and to leave out a posting or
job whose text could not be read whole. The checker gets the same files as the writer. A
session's trace adds the longest line the harness was given and, for the reader, how many
fields it filled and how many it called unknown; the files a tool reads there show as
`text`. Without `python3` on the Mac, `in.json` goes as JAP gave it.

**Devin** runs under JAP's own settings (`agent/harnesses/devin.config.json`), not yours: no
shell or web tool, nothing readable under your home folder, nothing writable outside the
batch folder, and `in.json` read-only. Its file reader cuts lines at 2,000 characters, so it
searches `in.json` to get at a long job description: one reader batch of 3 postings and a
pair took 9 minutes and 1.3 million input tokens, against 42 seconds and 208,000 for 10
postings on Claude Code. Check the tokens in `make agent-status` before making it the daily
harness.

**Switching harness** (AH7):

1. Install the new tool and sign in with your subscription (`codex`, `gemini`, `opencode`;
   Devin: `devin auth login`, and the adapter finds the copy inside Devin Desktop when
   `devin` is not on PATH).
2. `make agent-check HARNESS=codex` runs one small reader batch and the writer on the local
   stack, never live. Look at its output and at `data/agent/last-reader.log`.
3. Set `JAP_HARNESS=codex` (and `JAP_HARNESS_MODEL` if you want one) in `.env.live`.

The schedule reads the setting at each run, so nothing else changes; `make ENV=live
agent-run JOB=reader HARNESS=gemini` tries one run on live by hand. Only the Claude adapter
has run against real plans so far; the others follow each tool's documented non-interactive
mode and must pass `agent-check` before use. Answer quality depends on the model you pick;
the import checks, the checker pass and Review mode stay the same whatever the harness.
Some subscriptions do not allow unattended use: check your plan's terms.

## When something fails

- **A job fails** (the form page is gone, the form changed, Submit gave no confirmation): after
  Inngest's retries it shows **Failed** with the reason, a screenshot of the page as JAP saw it
  (also when the form couldn't even be read), and **Retry**.
- **Alert emails** go to the address on My profile, once each: when a run fails, when a board
  in a run can't be fetched, when an application ends Failed (with the screenshot attached),
  when the weekly form test can't read a form, and (from v0.30.0) when an AI reader or writer
  run fails, stops at the usage limit or reads nothing (once a day for each job). They never carry answers or letters.
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

## Logs, Health and Insights

PRD 1.19 (O1 to O15), Part A, and 1.33 (O16 to O23), Part C. Everything runs with the rest
of JAP and costs nothing. Two kinds of data leave, each only when its setting is filled in:
redacted errors to Sentry (`SENTRY_DSN`), and numbers about AI work to a trace service
(`JAP_TRACE_ENDPOINT` and `JAP_TRACE_HEADERS`), never text.

- **Logs**: the app and every Inngest function write one JSON line per event to stdout, with
  the ids it belongs to: request, run, application, posting, listing, Inngest run and
  function (`backend/observability/`). Every line is scrubbed first: email addresses, phone
  numbers, keys and tokens, `password=...` and the like, and emailed security codes are
  replaced, and fields such as answers, resume text and letters are never written.
  `LOG_JSON=false` prints plain lines instead. Docker keeps each container's lines capped at
  5 files of 50 MB (`make logs`). Every API response has an `X-Request-ID` header, so a
  problem can be found by its request.
- **Kept in Postgres**: warnings, errors and each apply step go to `log_event` for 30 days, and
  each API route's count, errors and time taken go to `route_stat` per hour. `make errors`
  shows the last 20 warnings and errors.
- **Settings > Health**: whether today's Daily search ran, searches stuck for over 30
  minutes, errors in the last 24 hours grouped by what failed, the slowest pages, disk use,
  and the alerts emailed in the last 7 days.
- **Health alerts**: `health-watch` runs every hour (at :40). It deletes log rows past 30 days
  and emails the address on My profile when the Daily search didn't start, a search is stuck,
  more than `JAP_ALERT_ERRORS_PER_HOUR` (10) errors happened in an hour, AI spend reached 80% or
  100% of the limit, or the disk is over 85% full. Each problem is emailed at most once a day
  (spend once a month). `JAP_ALERTS_ENABLED=false` turns them off; outside live the subject
  starts with the environment, for example `[DEV]`. **Check now** or `make health-check` runs
  it at once. Failed searches, applications and form tests keep their own alerts.
- **What JAP did**: a failed or sent job's panel has a timeline of its application: the form
  read, fields mapped, each AI use with its time and cost, submit, and the confirmation email.
- **Insights** (new page): jobs found, matching, started, sent and failed per week; results by
  job site with the commonest failure reasons; AI spend this month by use and model, and the
  last six months with the cost per sent application.
- **Sentry** (optional, free Developer plan): create a project at sentry.io, put its DSN in
  `SENTRY_DSN` and restart. Only errors are sent, with no request bodies, cookies, headers or
  local variables, and every message scrubbed.
- **Traces of AI work** (optional, PRD 1.33; LangSmith's free Developer plan first). Off
  unless both settings are filled in, and JAP works the same without them:
  `JAP_TRACE_ENDPOINT` is the service's OpenTelemetry address (LangSmith:
  `https://api.smith.langchain.com/otel`, `eu.api...` for the EU region; `/v1/traces` is added
  when it is not already there), and `JAP_TRACE_HEADERS` is `name=value,name=value` with the
  service's key (LangSmith: `x-api-key=<key>` with either `Langsmith-Project=jap`, or
  `Langsmith-Agent-Id=<id>` and `Langsmith-Agent-Environment=production` for an agent created
  in its UI). They are OpenTelemetry traces over HTTPS, written
  out by `backend/observability/trace.py`, the only module that talks to the service (a test
  checks it), with no service's library: moving to Sentry, Langfuse or Logfire is those two
  settings. What goes out:
  - one span per model call JAP makes itself (`api` mode): purpose, model, the provider that
    served it, prompt version, attempt, time, tokens in and out, cached and reasoning tokens,
    cost, and how it ended (`valid`, `retried`, `blocked`, `spend_cap`, `error`), with the run,
    application and posting ids. One application's calls are grouped, else one run's. The
    `llm_call` row holds the same, whether or not a service is set;
  - one span per export and import of the AI reader and writer (see
    [AI harness](#ai-harness)): counts and reason codes;
  - one trace per run of the runner (from v0.29.0, `backend/pipeline/agent_run_trace.py`):
    the run, each harness session with its export and import, and each model request (tokens
    in and out, cached) and tool call (the tool, the file of the batch folder, whether it
    worked), with the harness, the model, the job prompt's version, a failed session's cause,
    what was saved and left out, and the writer's and checker's reason codes as counts. No
    id of an application or posting goes out with it. Tokens are sent as usage on a model
    request only, so the service adds a run up once; a session with no record from its
    harness carries its estimate under a name that says it is one.

  Never text. The sender takes fields by name and keeps a value only when it is a number, one
  of JAP's ids, a value from a fixed list, or a short identifier such as a model name; a
  prompt, a job description, an answer, a company or a job title cannot pass, and a test
  fails the build if one does. The runner's trace file passes the same list: it is
  written on the Mac, and whatever is in it, only what the list knows goes out. Sending
  never delays or fails the work: spans wait on a queue for a background thread, a post that
  fails is dropped with one line on stderr, and an export or import waits at most 3 seconds
  for its own span before it exits. A run's trace is the exception to dropping: it is sent
  after the run's work, the runner is told whether the service took it, and one that was not
  taken is kept and sent at the start of the next run. JAP's own pages do not show these
  traces. Tried against LangSmith on 24 Nov 2026: a span was accepted and
  shows there as a model run with its tokens and JAP's fields, with no input or output text.
- **Uptime from outside** (O7): add a free uptime check (a Cloudflare health check, or a free
  monitor such as UptimeRobot) on `https://jap.tutorial.monster/api/health` every 5 minutes,
  emailing you when it fails. It runs outside JAP, so it still reports when the VM is down.

Part B comes after sign-in step 2: the user on every log line, Health for the admin only,
first-party usage counts (`usage_event`), each user's own Insights, and per-user counts and
spend for the admin.

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

## Sending email

Every JAP email (Applied, alerts, the form test) goes to the address on My profile, sent over
SMTP from the mailbox set in `.env`. `make email-test TO=you@example.com` sends one test email.

**GoDaddy Professional Email** (the default): `SMTP_HOST=smtpout.secureserver.net`,
`SMTP_PORT=465`, `SMTP_SSL=true`, `SMTP_USERNAME` = the full address (you@yourdomain.com) and
`SMTP_PASSWORD` = that mailbox's password. These are GoDaddy's
[IMAP/SMTP settings](https://www.godaddy.com/help/use-imap-settings-to-add-my-professional-email-to-a-client-32204).

**Gmail**: `SMTP_HOST=smtp.gmail.com`, port 465, your Gmail address, and an app password from
https://myaccount.google.com/apppasswords (needs 2-Step Verification). An older `.env` with
`GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` still works when `SMTP_USERNAME` is empty.

After changing `.env`, run `make restart`. A send that fails is retried, then logged; the job
stays Applied and its panel says the confirmation email has not gone out.

## Greenhouse security codes

Greenhouse sometimes asks for an 8-character code it emails before it takes an application
(PRD A5). JAP reads that code from its own mailbox (the one in `.env`) over IMAP, types it in
and presses Submit once more. Set it up once:

1. In Gmail, Settings, Forwarding and POP/IMAP, add your JAP mailbox as a forwarding address.
   Gmail emails it a confirmation link: open that email in the JAP mailbox (GoDaddy webmail)
   and click it.
2. In Gmail, create a filter: From `greenhouse`, Has the words `security code`, then Forward it
   to the JAP mailbox.
3. `make code-mail-check` logs in over IMAP and counts the Greenhouse emails it can see.

GoDaddy's IMAP server is the default (`IMAP_HOST=imap.secureserver.net`, 993, SSL); for Gmail
use `IMAP_HOST=imap.gmail.com`. JAP uses only Greenhouse emails that arrive after its Submit,
waits up to 3 minutes, and never stores the code or sends it to a model. No code in time: nothing
was sent and the job becomes Apply yourself.

## Hosting: local and live

**Both stacks run on a free-tier VM (Linux, ARM), not on the Mac** (PRD 1.32, H1 to H9). live
moved there on 21 Nov 2026 and local runs there too, so neither depends on the Mac being on
and awake. The Mac is where the code is edited and where the AI reader and writer start. The
VM is JAP's home for now: JAP stays on a free VM, so if this one leaves the free tier it
moves to another, and a serverless stack is a possible later move.

- **The code is edited on the Mac only.** The VM holds a copy of this checkout, kept in step
  one way, Mac to VM, every two minutes by a sync job on the Mac that is not part of this
  repo. local reloads from that copy. Do not edit on the VM.
- **Opening local.** Its ports are published on the VM's 127.0.0.1 only. An ssh port forward,
  kept up by a launchd job on the Mac, carries them across, so http://localhost:5173 and the
  other local addresses in this README open on the Mac as before. local is never published.
- **Running make from the Mac.** A target that only talks to running containers works with
  the VM as the Docker host: `DOCKER_HOST=ssh://oracle-vm make ps`, and the same for `logs`,
  `migrate`, `test` and `errors`. `make start` builds from the source and mounts it, so run
  it on the VM, in its copy of the checkout.
- **Data.** Each stack's database and data folder (`data/`, `data-live/`) are the VM's. The
  copies left on the Mac stopped at the move and are only a way back.
- **Ollama** runs natively on the VM, without a GPU, for embeddings only, and both stacks
  share it.
- **One live only.** On a Mac `make start`, `restart` and `reset` with `ENV=live`, and
  `make tunnel-start`, refuse. live is in Automatic mode: a second live would submit the same
  applications twice. Moving it back is deliberate: stop it on the VM first, then add
  `LIVE_ON_THIS_MAC=1`.
- **The tunnel on Linux** shares the host's network (`infra/tunnel/compose.linux.yaml`),
  because a bridge container there cannot reach the UI port, which is published on 127.0.0.1
  only.
- **What still needs the Mac.** The AI reader and writer are scheduled on the Mac (launchd,
  07:05 and 07:30) and reach live through `DOCKER_HOST` in their jobs;
  `DOCKER_HOST=ssh://oracle-vm make ENV=live agent-schedule` rewrites those jobs and keeps
  it. `make finish` opens its browser on the Mac and still expects `data-live/` there. The
  daily search, backups, the form test and the health watch run inside JAP on the VM and
  need no Mac.

JAP runs for free as two stacks, and JAP's own Cloudflare Tunnel publishes the live one over
HTTPS (PRD H1 to H9).

| | local | live |
| --- | --- | --- |
| Address | http://localhost:5173 | https://jap.tutorial.monster |
| Env file | `.env` | `.env.live` |
| Code | this working copy, live reload | built into the image; a file you edit changes nothing until the next `make start ENV=live` |
| UI | Vite dev server | `npm run build`, served by Caddy, which passes `/api` to the app |
| Entry / API / Inngest / DB ports | 5173 / 8001 / 8290 / 5432 | 5373 / 8201 / 8490 / 5434 |
| Data folder | `data/` | `data-live/` |
| Submits applications | **never** (`JAP_SUBMIT_ENABLED=false`) | **yes, for real** |
| Sends email | no | yes, the real mailbox |
| 06:00 daily search | leave off | turn on in Settings once you trust it |
| Adminer | yes, `:8080` | no |
| Restarts on its own | no | yes, with Docker, which starts when the VM boots |
| UI badge | **DEV** | none |

Both run from this one checkout and share nothing but the machine and Ollama: separate Docker
project (`jap`, `jap-live`), separate database volume, separate data folder, separate ports.
So `make reset` on local cannot touch a real application, and a broken edit cannot reach the
public site. Every target takes `ENV=local|live`; without it you get local, exactly as before
(`make logs ENV=live`, `make backup ENV=live`).

live adds `infra/compose.release.yaml`: the built UI behind Caddy so the browser only ever
talks to one address, a self-hosted Inngest with its own keys, no source mounts, no Adminer,
and `restart: unless-stopped` on every service. Only live's UI port goes through the tunnel;
the API port, Inngest's dashboard, Postgres, Adminer and Ollama are never published (H2),
and the local stack is never published at all.

**First time**

These steps were written for a Mac. On the VM, skip 1 and 2: Docker starts when the VM boots,
and the daily search does not wait on a Mac.

1. Docker Desktop: Settings, General, turn on *Start Docker Desktop when you sign in*.
2. `make mac-wake` so the Mac wakes at 05:55 for the 06:00 search.
3. `make start ENV=live`. The first run writes `.env.live` from `.env.example` plus
   `infra/env/live.env`, with a fresh database password and Inngest keys. Put the OpenRouter
   key and the real mailbox password in it, then run it again.
4. Cloudflare dashboard, Zero Trust, Networks, Tunnels: create a tunnel named `jap` and add
   one public hostname, `jap.tutorial.monster` to `http://host.docker.internal:5373`. Copy
   `infra/tunnel/.env.tunnel.example` to `infra/tunnel/.env.tunnel` and paste the token.
5. `make tunnel-start`, then open https://jap.tutorial.monster and create your account. live
   starts empty: it does not share the local stack's profile, resume or jobs.

Before the tunnel opens, turn on two-step sign-in (Settings > Account) and add a Cloudflare
rate-limiting rule on `/api/auth/*`. The sign-in page is then on the public internet, and the
per-account lockout is the only limit JAP itself applies.

**Releasing**

There is no promotion step: live runs whatever the VM's copy of this checkout builds.

1. Make the change and check it on local (`make start`, `make test`).
2. `make backup ENV=live` if the change has a migration.
3. `make start ENV=live`, on the VM, rebuilds the image, restarts live and applies migrations.
4. Something wrong: `git checkout <last good tag>` and `make start ENV=live` again. If a
   migration changed data, `make restore ENV=live FILE=data-live/backups/<file>.sql.gz`.

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
| `make close-unfollowed` | List open postings of companies you no longer follow; `APPLY=1` closes them (back up first) |
| `make finish [APP=<id>]` | Finish a job with a picture puzzle: opens the filled form in a visible browser on the Mac, you solve and submit, JAP checks the confirmation (JAP-11) |
| `make code-mail-check` | Log in to the mailbox over IMAP and count the Greenhouse emails it holds (security codes, PRD A5) |
| `make qa` | Dedup QA set: place the 106 labelled job pairs and print the accuracy |
| `make form-check` | Read one live form per ATS now (the weekly form test); nothing is submitted |
| `make set-password EMAIL=...` | Set a new password for an account from the machine (asks twice) |
| `make two-step-off EMAIL=...` | Turn two-step sign-in off for an account (phone and recovery codes lost) |
| `make sign-out-all EMAIL=...` | End every session of an account |
| `make health-check` | Run the hourly health watch now: old log rows deleted, alerts that are due emailed |
| `make errors` | The last 20 warnings and errors from `log_event` |
| `make email-test TO=...` | Send one test email from the mailbox in `.env` |
| `make agent-run JOB=reader\|writer [HARNESS=...]` | Run the AI reader or writer now on `JAP_HARNESS` (PRD 1.30) |
| `make agent-check HARNESS=...` | Try a harness on the local stack (one small reader batch and the writer), never live |
| `make ENV=live agent-schedule` / `agent-unschedule` / `agent-status` | The Mac's daily schedule for the AI reader and writer (launchd); `agent-status` also shows the last run's trace (sessions, time in the model, tokens, causes) |
| `make agent-trace-send FILE=...` | Hand a run's trace file (`data*/agent/traces/<run>-<job>.json`) to JAP, which sends it to the trace service when one is set; the runner does this itself after every run (PRD 1.34, O22) |
| `make test-agent` | The runner against a stand-in JAP and harness (no model, no Docker; part of `make test`) |
| `make reader-export` / `make reader-import FILE=...` / `reader-status` | One batch of unread postings for the AI reader, redacted; its fields back in, checked (PRD 1.25) |
| `make writer-export` / `make writer-import FILE=...` / `writer-status` | Jobs at Needs you for the AI writer, redacted; its answers back in (PRD 1.22) |
| `make llm-check` | One tiny test call to each OpenRouter model (no personal data) |
| `make ollama-check` | Is Ollama answering with `nomic-embed-text` pulled |
| `make demo` / `make demo-clear` | Load or remove the demo postings used to check dedup and the filters |
| `make env ENV=live` | Write `.env.live` with a fresh database password and Inngest keys (`make start ENV=live` does it too) |
| `make tunnel-start` / `tunnel-stop` / `tunnel-status` | JAP's Cloudflare Tunnel (`.env.tunnel`) |
| `make mac-wake` | Wake the Mac at 05:55 every day for the 06:00 search |

## Layout

```
Makefile            .env.example
infra/              compose.yaml, compose.release.yaml (live), docker/ (Dockerfiles),
                    caddy/, env/live.env, tunnel/, postgres/init.sql, scripts/
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
| 0.32.0 | 25 Nov 2026 | The AI reads and writes less (PRD 1.37: R3, W8, W13). **Reader**: only postings in your target roles are given out when your match rule needs a role in every combination (`JAP_READER_SCOPE`, default `roles`); `reader-status` says `unread` and `unread_all`; the export adds `unread_to_read` and `scope`. **Writer**: the runner sends `_handed_out` with the answers, and JAP notes each job left out with a hash of its inputs (migration 0015, `application.writer_left_out_at` and `writer_left_out_inputs`), skips it until they change or 7 days pass, says so on its questions, and counts it as `waiting_on_you`. **Checker**: `agent/draft.py` skips the checker session for a draft of choices only (`JAP_CHECKER_WHEN`, default `text`). Contract stays 1 (fields added). `make test-agent`: 103 checks. |
| 0.31.0 | 25 Nov 2026 | The AI reads every description and the resume whole (PRD 1.36, AH10; prompts reader 1.1, writer 1.4, checker 1.4). `agent/wrap.py`, called by the runner after each export, moves every string over 600 characters into `text/` beside `in.json`, wrapped at 100 characters, with a pointer left in `in.json`; the import is unchanged. The prompts say to read those files to the end and to leave out what could not be read whole. Devin may not write under `text/`. The trace gains `longest_line` per session, `fields` and `unknown` for a reader, and the file `text` for a tool call; the sender's allow-list and the backend's copy of a run trace follow. `make test-agent`: 100 checks. |
| 0.30.0 | 25 Nov 2026 | AI runs that finish, and an email when one does not (PRD 1.35: AH4, AH6, O18, O25). Found from the runs of 21 to 25 Nov 2026 and the Mac's power log: the runner ran on a Mac asleep on battery, woken 20 seconds every 10 minutes, and every export hung half an hour on a dead ssh connection. **Calls to JAP**: `agent/run.sh` stops each status, export and import after 2 minutes (`JAP_AGENT_CALL_S`) and tries one that did not reach JAP again, 3 tries 30 seconds apart (`JAP_AGENT_CALL_TRIES`, `JAP_AGENT_RETRY_S`); one JAP answered is not retried. **Causes**: `unreachable` is new, and a call that never reached JAP is `slept` (the Mac woke after it began) or `unreachable`, no longer `export_failed` or `import_failed`. **Schedule**: `agent/schedule.sh` tries each job every hour until 22:00 until one run of it has finished that day, a scheduled run does not start in a dark wake, and runs keep the Mac awake on power (`caffeinate -is`); reinstall with `make ENV=live agent-schedule`. **Alert** (O25): `backend/pipeline/agent_run_alert.py`, called by the trace door, emails when a run failed, hit the usage limit, or read nothing, once a day per job. `make test-agent` grows from 84 to 96 checks; new backend tests in `test_agent_run_alert.py`. **JAP-14 fixed**: the runner's watchdog could mark a session that had worked as timed out (about one run in two on Linux), which failed a runner check now and then; only a full sleep now stops a session. |
| 0.29.0 | 24 Nov 2026 | The trace of AI work, third of three releases (PRD 1.34: O22, O24, and O20 completed). **The runner's trace is sent**: after each run `agent/run.sh` hands the run's trace file to JAP (`make agent-trace-send`, `backend/pipeline/agent_run_trace.py`), which sends it as one trace: the run, each session with its export and import, each model request and tool call. It passes the same allow-list as every other span, so a trace file with text in it sends none of it, and no application or posting id goes out. The runner never holds the service's key. A trace that could not be sent stays marked `.unsent` beside its file and is handed over again at the start of the next run; handing over is stopped after a minute and never changes how a run ends. `observability/trace.py` gains `send_tree` (parents and children, ids worked out from the run's stamp, so a second send carries the same ids) and the fields of a run on its allow-list. **Why a job was left out, in codes**: the writer and checker prompts (version 1.3) ask for `reasons.json`, what happened to each job, why, and which kind of fact the answer bank lacked, from fixed lists; the runner keeps only those codes for ids of the export, counts the jobs left out itself, and writes a line per job to the run log. **O20**: a session's trace names the job prompt's version and the contract version. `make test-agent` grows from 67 to 84 checks, one of which keeps the codes the same in the prompts, the runner and the sender. Tried against LangSmith from the local stack with the tests' two stand-in runs (accepted, 9 and 15 spans); not yet by a run with a real harness. |
| 0.28.0 | 24 Nov 2026 | The trace of AI work, second of three releases (PRD 1.33: O17 level 1, O19, O22, O23). **A sender**: `backend/observability/trace.py` is the only module that talks to a trace service, as OpenTelemetry traces over HTTPS with no service's library, off unless `JAP_TRACE_ENDPOINT` and `JAP_TRACE_HEADERS` are both set (LangSmith's free plan first). It takes fields by name and lets through only numbers, JAP's ids, values from fixed lists and short identifiers such as a model name, so no prompt, job text, answer, company or title can be sent; a test fails the build if text gets through. It never delays or fails the work: spans are queued and posted by a background thread, and a post that fails is dropped. **Model calls JAP makes itself** (`api` mode): each `llm_call` row now also holds the posting, the provider that served it, the attempt, cached and reasoning tokens and how it ended (`valid`, `retried`, `blocked`, `spend_cap`, `error`), and the same goes out as one span per call, the calls of one application or one run grouped. **JAP's doors**: every reader and writer export and import leaves one `log_event` row (`agent.export`, `agent.import`) and one span with counts only: items handed out, the export's size, items saved, and why the others were not, as codes. The runner takes the trace settings out of a harness's environment. Migration `0014_llm_call_trace`. Tried against LangSmith on 24 Nov 2026: a span was accepted and shows there as a model run with its tokens and JAP's fields, and no input or output text. The runner's own traces are sent in the next release |
| 0.27.0 | 24 Nov 2026 | AI runs leave a trace (PRD 1.33, O16 to O21, first of three releases; nothing leaves the Mac yet). `agent/trace.py` and the runner record every reader, writer and checker session in numbers only: start, end, exit, what the import saved, and one named cause when it failed (`not_logged_in`, `slept`, `timeout`, `usage_limit`, `no_output`, `export_failed`, `import_failed`), where three different failures on 21 to 23 Nov all read "wrote no out.json". Where the harness gives a record of its session, each model request and tool call is kept with its time and tokens, and the run log and `make agent-status` show them; a harness without one gets an estimate from the file sizes. One trace file per run in `data*/agent/traces/` (30 days), one per session in its batch folder. A fifth adapter, for Devin's command line tool. A usage limit is read from the harness's record or its failing last words: Claude Code's "You've hit your weekly limit" was not recognised before, and a reply that quoted the word quota stopped a run. `JAP_AGENT_TRACE=0` runs the harnesses as before. The Devin adapter runs under JAP's own settings file (no shell, no web, `in.json` read-only), and the checker always gets the export as JAP gave it. Proven by a run: Devin (`make agent-check HARNESS=devin`) and Claude Code's limit reply; the other records are read from each tool's documents until `make agent-check` proves them |
| 0.26.2 | 23 Nov 2026 | The scheduled AI reader and writer now run under `caffeinate` so the Mac cannot idle sleep mid-run (a sleep broke the ssh to the live VM on 22 and 23 Nov), and `make ENV=live agent-schedule` writes a set `DOCKER_HOST` into the launchd jobs instead of dropping it. Every harness session also gets `docs/failures.md`, a shared log of failed applications and the lessons from them, copied into its batch folder (agent prompts 1.2). Both stacks now run on a free-tier VM (PRD 1.32, H1 to H9; see Hosting): on a Mac `make start`, `restart` and `reset` with `ENV=live`, and `make tunnel-start`, refuse unless `LIVE_ON_THIS_MAC=1`, and on Linux the tunnel shares the host's network (`infra/tunnel/compose.linux.yaml`). The PRD is now kept as Markdown in `docs/` and the build plan is retired |
| 0.26.1 | 20 Nov 2026 | A JAP icon in the browser tab: a white J on the app's teal, as `favicon.svg` with a 32 px PNG fallback and a 180 px home-screen icon, in `frontend/public/`. The tab title names the screen and counts the jobs that need you, `(3) Jobs · JAP`, with `[TEST]` or `[LOCAL]` in front on a copy that isn't prod, and reads `Sign in · JAP` before sign-in |
| 0.26.0 | 19 Nov 2026 | Company limits in the UI (PRD 1.31, V10). Job preferences lists every company with a limit of its own: the limit, the jobs sent inside the current window with their dates, and the exact date and time, in `JAP_TZ`, that it opens again. The open date is when enough sends have left the window to make room, not the oldest send plus the window. `GET /api/boards/limits` |
| 0.25.0 | 19 Nov 2026 | Fix JAP-13: Supabase allows 3 applications per person in 60 days, and its Ashby form quietly dropped the 5th and 6th (Turso, Spark) with no error, so JAP saw only "did not confirm". JAP now reads such a limit off the form (migration `0013_company_limit`: `company.apply_limit`, `apply_limit_days`), fails an attempt past it before Submit with the reason, re-checks just before Submit, and passes over that company in pick until the window has room. The per-ATS and global submit gaps now also count a pressed submit that got no confirmation |
| 0.24.1 | 19 Nov 2026 | Fix JAP-12: an application that passed read-form before v0.23.0 and was waiting when v0.23.0 was deployed failed with "'int' is not iterable" when it resumed, because the stored read-form result was the old field count. apply-listing now accepts both shapes |
| 0.24.0 | 19 Nov 2026 | Swappable AI harness (PRD 1.30, AH1 to AH8). The AI reader and writer run on any agent harness and subscription: `JAP_HARNESS` picks Claude Code, Codex, Gemini CLI or opencode. A runner (`agent/run.sh`, `make agent-run`) does the exports, imports, 8 parallel reader shards, leftover pass and usage-limit stop itself, and adds a checker session before writer answers go back; the harness only reads `in.json` and writes one file. Harness-neutral job prompts and JSON Schemas live in `agent/`, exports carry `contract_version` 1, and `make ENV=live agent-schedule` puts the daily reader and writer on the Mac's launchd instead of the Claude app. `make agent-check HARNESS=...` tries a harness on the local stack. Modes renamed `agent` and `api` (`claude` and `openrouter` still work); Needs you says "Waiting for the AI writer" |
| 0.23.1 | 19 Nov 2026 | Fix: `writer-export` was refused on live ("personal detail in model input (name)") because the new `review_rule` text named the owner. It now names no one, and a test exports with an owner name set |
| 0.23.0 | 19 Nov 2026 | Automatic at volume (PRD 1.29, V1 to V8). Automatic runs pick matching jobs in random order, at most 2 attempts a day per company and 8 submits a day per ATS, 5 minutes apart per ATS plus a per-job jitter on the 30 second gap. A 429 or 403 (or "too many requests") puts the ATS on hold for 1, 4, then 24 hours (migration `0012_board_pacing`: `ats_hold`); the job waits instead of failing and the Runs history shows the hold. The Claude writer now answers Automatic jobs at Needs you too, and a job it flags (`"_review"` in `writer-import`, kept as `application.hold_reason`) or a form that bans AI-written answers stops at Ready for review, never sent on its own. A run picks the cap plus 5 extra, less what is in flight |
| 0.22.0 | 19 Nov 2026 | Everything on Claude (PRD 1.28). `JAP_READER=claude` also hands Possible duplicates to the Claude reader: `reader-export` (shard 1) carries the open pairs as job text with the same-job rules, and `reader-import` takes `pair:<id>` decisions and merges or keeps apart through the same step. `JAP_WRITER=claude` also leaves a required cover letter at Needs you for the Claude writer (marked `cover_letter`, with `cover_letter_rules`; JAP builds the PDF from its text) and leaves a new resume at Reading for it (`resumes` in `writer-export`, `resume:<id>` in `writer-import`). With both on claude, JAP makes no OpenRouter call |
| 0.21.0 | 19 Nov 2026 | Claude writer only (PRD 1.27, W10). `JAP_WRITER=claude` (the default) stops JAP sending form questions to OpenRouter: a required free-text question with no saved answer waits at Needs you as "Waiting for the Claude writer" for the scheduled writer (`make writer-export` / `writer-import`), and is not counted as a failed draft. `JAP_WRITER=openrouter` brings back JAP's own drafting. Cover letters and the duplicate check still use OpenRouter |
| 0.20.0 | 18 Nov 2026 | Claude reader (PRD 1.25, R1 to R7). Job postings are read for skills, seniority, salary, visa sponsorship, remote regions and whether a cover letter is asked by a scheduled Claude session on your Claude plan instead of OpenRouter, so reading needs no API credit. `JAP_READER=claude` (the default) stops JAP sending descriptions to OpenRouter; what the ATS states is still copied straight away. `make reader-export` gives the session a batch of unread postings (job text only, redacted and guarded; titles in your target roles first, and in disjoint shards so 8 readers can work at once); `make reader-import FILE=...` checks its fields against the extraction schema, saves the postings that pass for the description that was exported, and refreshes their jobs. `JAP_READER=openrouter` brings back JAP's own read as the fallback |
| 0.19.2 | 18 Nov 2026 | PRD 1.26 (A6). Fix half-filled Ashby applications (JAP-11), found on Atlan's form. Every Ashby field type is read and marked required from the question's title: Yes/No and multiple-choice radios, the location typeahead (typed into, closest place picked) and checkboxes. Each Yes/No button answer is clicked under its own question (the visa question's "No" had landed on "eligible to work"). A phone on a form with no country picker gets its country code. Just before Submit the form is read again, and an empty required field, a choice showing another answer or a CAPTCHA stops it with the reason. A picture puzzle is never solved: JAP fills the form, stops before Submit and the job shows **Finish yourself: puzzle**; `make finish` opens the filled form in a visible browser on the Mac, you solve and submit, and JAP checks the confirmation and marks it Applied (or press I sent it) |
| 0.19.1 | 18 Nov 2026 | PRD 1.24 (I2). Fix remote jobs failing Location when the model had not read them (JAP-10), as when the OpenRouter key is out of credit. The work mode and a remote job's regions now come from what the ATS states, read from the stored payload on every listing refresh: Ashby and Lever workplace type, Ashby isRemote, or a location entry saying remote ("Remote - India", or "India" beside "Remote"). Migration 0011 fills them once for the stored listings, keeping values set by hand, and filters them again. A job that says remote only in its description still needs the model |
| 0.19.0 | 18 Nov 2026 | Claude writer (PRD 1.22, W1 to W7). The Daily run prepares the new jobs that fit in Review mode too, not only in Automatic mode: they wait at Ready for review, or at Needs you when JAP cannot write an answer itself (no AI key credit). `make writer-export` gives a scheduled Claude session the jobs at Needs you (frozen description, open questions, resume, answer bank) with name, email, phone, address and profile links replaced by placeholders, and refuses if anything personal is left; `make writer-import FILE=...` puts its answers back with the real values filled in locally, through the same step as the Needs you panel. Nothing is approved or sent by either; drafted answers are not saved to the answer bank |
| 0.18.3 | 18 Nov 2026 | Fix two faults found sending the first Supabase (Ashby) applications. A page that already thanked you before Submit counted as a confirmation (JAP-8): Ashby shows "Thanks for your interest" in a banner on every form, so JAP called a job Applied while the Submit button was still spinning. Only words of thanks the page did not show before Submit count now, and JAP waits for them. Ashby's Yes/No questions were not read (JAP-9): their buttons have no type attribute and sit beside a hidden checkbox, so a required question such as "Are you over the age of 18?" went unanswered. They are read now, marked required from the question's title, and clicked like other button questions |
| 0.18.2 | 18 Nov 2026 | Fix every Ashby application failing with "No application form found on the page" (JAP-7). Ashby's hosted page shows "Fetching application form" and draws the fields a moment after the network goes quiet, so JAP read an empty page; the weekly form test had failed on Ashby for the same reason. The reader now also waits, up to 15 seconds, for a visible form field (not the hidden resume-autofill file input Ashby attaches first) before reading. A page with no form at all still fails, after that wait |
| 0.18.1 | 13 Nov 2026 | Fix the four settings you type rather than tick saving only when the field lost focus (JAP-6). Settings says changes save straight away, and every radio and switch does, but "Send at most N applications a day", "Only jobs with at least N% of my required skills", the daily search time and the monthly AI limit waited for a click somewhere else: Enter did nothing at all and there was no button, so a typed number looked ignored. Enter saves now, so does a Save button that appears beside the field as soon as the value differs from the saved one, Escape forgets the edit, and leaving the field still saves so nothing typed is lost. What was typed stays on screen until the save comes back, instead of showing the old value again in between |
| 0.18.0 | 13 Nov 2026 | Skills match instead of a skills filter (PRD 1.21, F5, F6, U15, U17, U18). Skills never hide a job: the Skills condition and the third Matching choice are gone, so matching is job title and location only, and the default rule is job title and location everywhere (the code fallback and the API default used to be any one of title, skills or location). Every job gets a skills match in the same SQL pass as the filters (migration `0010_skills_match`: `listing.required_hit`, `nice_hit`, `required_pct`): Required x of y with the percentage and the missing skills, Nice to have x of y, and Skills not stated when the description gave none, which is unknown and never 0. Jobs has a Skills match column that sorts, and a minimum filter (`sort=skills`, `min_skills`); the job panel lists the skills behind it. Automatic mode picks only jobs at or above a required-skills bar, 60% by default, set on Settings (`profile.auto_apply_min_skills`); the rest stay listed. A rule saved earlier with skills keeps its other conditions. |
| 0.17.1 | 13 Nov 2026 | Fix a job whose title is wider than the Job title column showing as nothing but `...` (JAP-5). The title is a button so it can be opened from the keyboard, and a button is `display: inline-flex`, which makes it an atomic inline box: `text-overflow` can shorten text but cannot cut into one. Chrome therefore dropped the whole button and drew the ellipsis in its place, so the row lost its title entirely while the side panel still showed it. The row is a flex line now and the button shortens its own text, so a long title reads "Applied AI Architect, Democrati…" |
| 0.17.0 | 13 Nov 2026 | Unfollow and re-follow (PRD 1.20, I10 to I18). Stopping following a company used to only switch its board off: its postings stayed open for ever, were never read, and kept "Reading N job descriptions" and "Searching…" on screen. Now it closes the board's open postings with the reason `unfollowed` (migration `0009_close_reason`; the other reason is `gone_from_board`), closes the jobs left with no open posting on a followed board, and nothing is deleted. Jobs with an application stay in the list with a note. No AI reading is done for an unfollowed company, queued reads included, and a search that was running cannot reopen its jobs. Following again switches the same board back on and the next search reopens the same rows, re-reading only descriptions that changed. A confirmation says how many jobs will be hidden; the search line says how many were reopened. `make close-unfollowed` closes what earlier unfollows left open |
| 0.16.2 | 13 Nov 2026 | Fix Profile claiming there is no resume for about a second on every visit. Switching tabs unmounts the page, so its resume list started empty again each time and the section drew "No resume yet. JAP needs one before it can apply.", without the skills found in the resume, until `/api/resumes` answered. On live that is roughly a second, because every call crosses the Cloudflare Tunnel, while locally it is 5ms, which is why it never showed in dev. Profile now paints the active version the store already caches and only says there is no resume once the list has really arrived; Saved answers waits the same way instead of flashing "No saved answers yet". An upload writes the new version back to the store, so the next visit paints that one and not the one before it |
| 0.16.1 | 12 Nov 2026 | Fix the foot of the sidebar: a long email address stretched the single grid column past the panel, so the line above Sign out and the Sign out button itself were drawn over the work area. The column is `minmax(0, 1fr)`, so the address is cut with an ellipsis (the full one stays in the tooltip) and the row ends where the panel does. Dropdown arrows no longer crowd the field border: Chrome pins the native arrow 4 or 5px from the edge whatever the padding is, so every select draws its own chevron at the same 10px inset as its label, which also makes selects exactly as tall as the search box beside them. The version under the logo said 0.15.0 |
| 0.16.0 | 4 Nov 2026 | Two environments instead of three (PRD H1 to H8 revised): `ENV=local\|live`, both from this one checkout, with their own env file, Docker project, database volume, data folder (`data/`, `data-live/`) and ports. `live` is the release stack behind the Cloudflare Tunnel at jap.tutorial.monster and the only one that submits or sends email; `local` never does either. Removed the test environment and the git worktrees in `~/jap-envs`, with `make env-create`, `promote`, `mark-passed` and `rollback`; a release is now `make start ENV=live`. `JAP_DATA_HOST_DIR` keeps the two data folders apart. README says plainly that JAP is a personal, single-user tool |
| 0.15.0 | 2 Nov 2026 | Logs, Health and Insights (PRD 1.19, Part A: O1 to O12, O15): JSON log lines with request, run, application and Inngest ids, scrubbed of personal data and secrets; warnings, errors and apply steps kept 30 days in `log_event`, API timings per route in `route_stat`; Docker log cap; `X-Request-ID`; Settings > Health; hourly `health-watch` with alert emails at most once a day; a timeline on each application; the Insights page; optional Sentry (`SENTRY_DSN`); `make health-check` and `make errors`; migration `0008_observability` |
| 0.14.0 | 2 Nov 2026 | Hosting with dev, test and prod (PRD 1.17, H1 to H8): every make target takes `ENV=dev\|test\|prod`; test and prod run from git worktrees in `~/jap-envs` with `infra/compose.release.yaml` (built UI behind Caddy, self-hosted Inngest, no Adminer, restart on Docker start); `make env-create`, `promote`, `mark-passed`, `rollback`, `tunnel-start/stop/status`, `mac-wake`; the UI calls `/api` on its own address; `JAP_ENV` badge; `JAP_SUBMIT_ENABLED=false` in test never submits |
| 0.13.0 | 2 Nov 2026 | Sign-in and accounts, step 1 (PRD 1.18, S1 to S9, U21). Sign-in screen before anything loads: create the first account (the admin; sign-up then closes), sign in, Forgot password with an emailed single-use 30-minute link, and the two-step code. Settings > Account: Change password, Two-step sign-in with a QR code and 8 recovery codes, Sign out and Sign out everywhere; Sign out also at the foot of the sidebar. Backend: `app_user`, `auth_session` and `password_reset` (migration 0007), scrypt hashes, a 15-minute lock after 5 wrong tries, TOTP with replay protection, HttpOnly host-only session cookies (7 days idle, 30 days at most; `__Host-` and Secure with `JAP_COOKIE_SECURE`), every `/api` route but health and `/api/auth/*` behind a session, `/api/inngest` closed to proxied requests, writes from other origins refused. `make set-password`, `make two-step-off`, `make sign-out-all`; `make form-check` now runs inside the app container |
| 0.12.0 | 31 Oct 2026 | Greenhouse emailed security code (PRD 1.11, A5): when the form asks for the code it emailed, JAP reads it from its own mailbox over IMAP (`IMAP_HOST`, `IMAP_PORT`, `IMAP_SSL`, same login as SMTP) after a Gmail filter forwards it there, using only Greenhouse emails that arrive after Submit and waiting up to 3 minutes; types it into the boxes and presses Submit once more; no code, nowhere to type it or a code the site refuses means nothing was sent and the job becomes Apply yourself; `make code-mail-check`; Greenhouse security codes section |
| 0.11.2 | 31 Oct 2026 | Apply fixes from the first real Automatic runs. Greenhouse: a combobox counts as filled when it changed from the fresh page (the phone Country picker shows only "+91"), values are read back after a short settle, a phone the page reformats still matches, and a field that won't keep its value is named with what the page shows. Ashby: Yes/No questions made of buttons are read as a choice (answer bank or Needs input) and filled by clicking the button; the page's own error text after Submit is in the error, and a "required" or spam-check error counts as not sent. "Successfully submitted" wording is taken as a confirmation. Greenhouse's emailed security code step is recognised and reported as not sent, to apply yourself |
| 0.11.1 | 31 Oct 2026 | Fix "OpenRouter 402" on drafted answers: every model call now sets `max_tokens` (about 3 characters a token plus room for the JSON for an answer, a fixed cap per purpose otherwise) instead of reserving the model's whole 64,000-token output against the credits; a 402 shows as "OpenRouter is out of credits" without the key link, stops further drafts on the same form, fails the cover letter without a retry and does not retry posting extraction or resume parsing; a reply cut off at the cap is reported as such |
| 0.11.0 | 31 Oct 2026 | Email from any SMTP mailbox (PRD 1.10, E4): `SMTP_USERNAME` and `SMTP_PASSWORD` with GoDaddy Professional Email as the default server (`smtpout.secureserver.net`, 465, SSL), Gmail with an app password as the alternative, old `GMAIL_*` settings still read; `make email-test`; Sending email section |
| 0.10.0 | 30 Oct 2026 | New design (PRD 1.5, mockup v0.5). Sidebar with Jobs, Profile, Job preferences and Settings in place of the Dashboard and My profile tabs; Jobs with a status strip, search first, Job title, Location, Work mode, Posted and Fits on filters, sortable columns, rows per page and page numbers, tick-to-Apply and Hide with Undo, a one-line duplicates bar and an AI limit banner; one job panel whose actions follow the status (Apply, Save and continue, Send application, Try again, Apply yourself, Undo hide) with Why it fits and Not the same job; Find new jobs with an arrow for some job titles and Also apply; Job preferences with office cities and On-site/Hybrid ticks, remote jobs as none, your countries or all (`profile.remote_any`, migration 0006), three Matching choices (a new profile starts with job title and location), and a save bar that counts the jobs that will fit; Settings with Applying, Daily search, Search history, AI usage and System status; contact-only saves start no search; `GET /api/listings` takes sort, dir, work_mode, posted_days and status=active, and search covers locations; while testing: a / tooltip on the search box, the last search line coloured by state, and borderline duplicates settled by the AI instead of asking (no Merge list; Not the same job stays) |
| 0.9.2 | 30 Oct 2026 | Fix: Approve on Greenhouse job-boards pages left every field empty. Submit waits for the page to settle, reads every value back and fills again what the page cleared, and stops before Submit (nothing sent) when a field still won't keep its value; dropdowns built from a text input (comboboxes) are read as selects with their options and filled by picking the option; a form that refuses the submission is reported as not sent; Retry after a Submit whose outcome is unknown asks you to check the employer's site first |
| 0.9.1 | 30 Oct 2026 | Fix: Apply now didn't draft free-text answers. The answer prompt (now `answer.v2`) asks for JSON like every other prompt, `llm.call()` accepts JSON in a code fence or a sentence and logs a reply that isn't JSON as an error, a draft the writer model can't make shows as "JAP couldn't draft an answer" with the reason on Needs input (with a text box to write your own) and as "answers not drafted" on the last run line |
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
