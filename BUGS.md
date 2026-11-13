# Bugs and issues

One serial list. A number is assigned when the defect is first reported, never changes and is
never reused, so `JAP-2` means the same thing in a commit subject, a branch name and a
conversation six weeks later. Aditya decides whether something is a **Bug** (JAP does the
wrong thing) or an **Issue** (JAP does what it was built to do, and that is the problem); the
number does not encode which, because that judgement can change and the number must not.

The `JAP-` prefix is there because commit subjects already carry GitHub PR numbers in the same
shape — `Fix OpenRouter 402 (v0.11.1) ... (#13)` — and GitHub turns a bare `#2` into a link to
pull request 2.

Status is `Open`, `Fixed (vX.Y.Z)` or `Closed` with the reason. Entries are closed in the same
commit that fixes them.

| Id | Type | Status | What |
| --- | --- | --- | --- |
| [JAP-1](#jap-1--bug--profile-flashed-no-resume-yet-on-every-visit) | Bug | Fixed (v0.16.2) | Profile flashed "No resume yet" for a second on every visit |
| [JAP-2](#jap-2--bug--a-spent-openrouter-key-is-retried-until-it-fills-the-log) | Bug | Open | A spent OpenRouter key is retried until it fills the log |
| [JAP-3](#jap-3--bug--the-skills-matching-preset-can-never-match-with-an-empty-required-list) | Bug | Open | The skills matching preset can never match with an empty Required list |
| [JAP-4](#jap-4--issue--jap_env-still-says-dev-and-prod-after-the-locallive-rename) | Issue | Open | `JAP_ENV` still says `dev` and `prod` after the local/live rename |

---

## JAP-1 — Bug — Profile flashed "No resume yet" on every visit

**Status:** Fixed (v0.16.2), commit `41dac3f`. Reported 13 Nov 2026.

Switching to Profile from any other tab drew "No resume yet. JAP needs one before it can
apply.", with the skills found in the resume missing, for about a second before the real
resume appeared. Worse than a blank: it asserted JAP could not apply and offered a primary
"Upload resume" button for a problem that did not exist.

`App.tsx` renders pages with `{view === 'profile' && <ProfilePage />}`, so leaving the tab
unmounts the page and all of its state. `ResumeSection` held the resume list in that state,
starting at `[]` and refetching on mount, with no loading state — so "empty" and "not known
yet" rendered the same, and the code picked the alarming reading. Meanwhile the store already
cached the active version in `store.resume` and `JobsPage` was already using it.

Only visible on live because `/api/resumes` takes ~0.9s through the Cloudflare Tunnel against
~5ms locally. The same bug was there in dev, on screen for one frame.

Fixed by making the list `Resume[] | null` (`null` = not known yet, distinct from empty),
painting the store's cached active version until the list arrives, and saying there is no
resume only once the list has really answered. Saved answers had the identical flash and got
the same guard. An upload now writes the new version back to the store (`setResume`) so the
cache cannot go stale.

---

## JAP-2 — Bug — A spent OpenRouter key is retried until it fills the log

**Status:** Open. Found 12 Nov 2026 while tracing 954 errors on Settings.

When the OpenRouter key hits its credit limit the API answers `403 Key limit exceeded (total
limit)`. That is permanent — no amount of retrying helps — but JAP treats it as a transient
failure and retries it everywhere, at once:

- 944 `enrich-posting` runs failed in a single hour, each burning its full `retries=2`
- every exhausted run returns `POST /api/inngest 500`, logged at error level
- 3,699 error rows and 15,061 warnings accumulated on live; Settings showed 954 in 24h
- alert emails fire, because the threshold is 10 errors an hour

`backend/llm/client.py` maps only **402** to `OutOfCredits`, the error that callers treat as
fatal. A 403 falls through to a generic `ModelCallError`, and `extract_step` in
`backend/functions/enrich_posting.py` only converts `SpendCapReached`, `MissingApiKey`,
`OutOfCredits` and `PersonalDataBlocked` into `NonRetriableError`. The file's own docstring
says "a spend cap, a missing key or a blocked request stops this posting without retrying" — a
spent key belongs in that list.

Fix: map a 403 whose message says the limit is exceeded onto `OutOfCredits`, matching on the
message so genuine 403s (moderation, content policy) stay retryable. Needs a test.

Not the same thing as the key being out of credits, which is operational and fixed by raising
the limit at openrouter.ai/settings/keys. This entry is about the retry storm it causes.

---

## JAP-3 — Bug — The skills matching preset can never match with an empty Required list

**Status:** Open. Found 12 Nov 2026.

The Matching preset "Job title and location fit, and it asks for your required skills" stays
selectable when the Required skills list is empty, and then matches nothing at all, silently.

The condition is `cardinality(:must) > 0 AND l.skills @> :must` in
`backend/pipeline/matching.py`. With an empty list the first half is false, so the skills leg
is false for every job forever. Emptying the Required box does not switch the condition off,
it makes it unanswerable — which reads as "JAP is broken", because Jobs goes to zero with no
explanation anywhere on the page.

Fix: warn on that radio when `must_have_skills` is empty, in the spirit of the existing work
mode readback ("Tick On-site or Hybrid, or show remote jobs: as it is, no job can show."),
which already handles the equivalent dead end for work modes. `PreferencesPage.tsx`.

---

## JAP-4 — Issue — `JAP_ENV` still says `dev` and `prod` after the local/live rename

**Status:** Open. Noticed 12 Nov 2026.

v0.16.0 renamed the environments to `local` and `live` everywhere in the docs and the
Makefile, but the value of `JAP_ENV` was left behind: the local stack reports `dev` and the
live stack reports `prod`. It shows in the header badge, in every log line's `env` field, and
in `/api/health`.

The badge logic in `App.tsx` hides the badge when `health.env === 'prod'`, so renaming the
value to `live` without touching that condition would start showing a badge on the live site.
Both need changing together, and the log and alert text should be read through for the old
words at the same time.
