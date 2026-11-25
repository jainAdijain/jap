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
| [JAP-5](#jap-5--bug--a-job-title-wider-than-its-column-showed-as-nothing-but) | Bug | Fixed (v0.17.1) | A job title wider than its column showed as nothing but `...` |
| [JAP-6](#jap-6--bug--settings-you-type-saved-only-when-the-field-lost-focus) | Bug | Fixed (v0.18.1) | Settings you type saved only when the field lost focus |
| [JAP-7](#jap-7--bug--every-ashby-form-read-as-no-application-form-found) | Bug | Fixed (v0.18.2) | Every Ashby form read as "No application form found" |
| [JAP-8](#jap-8--bug--thanks-already-on-the-page-counted-as-a-confirmation) | Bug | Fixed (v0.18.3) | Thanks already on the page counted as a confirmation |
| [JAP-9](#jap-9--bug--ashby-yesno-questions-were-not-read) | Bug | Fixed (v0.18.3) | Ashby Yes/No questions were not read |
| [JAP-10](#jap-10--bug--remote-jobs-failed-location-when-the-model-had-not-read-them) | Bug | Fixed (v0.19.1) | Remote jobs failed Location when the model had not read them |
| [JAP-11](#jap-11--bug--ashby-applications-were-sent-half-filled) | Bug | Fixed (v0.19.2) | Ashby applications were sent half filled |
| [JAP-12](#jap-12--bug--applications-waiting-across-the-v0230-deploy-failed-with-int-is-not-iterable) | Bug | Fixed (v0.24.1) | Applications waiting across the v0.23.0 deploy failed with "'int' is not iterable" |
| [JAP-13](#jap-13--bug--supabase-silently-dropped-applications-past-its-own-limit) | Bug | Fixed (v0.25.0) | Supabase silently dropped applications past its own limit |
| [JAP-14](#jap-14--bug--a-runner-check-on-the-run-log-fails-now-and-then) | Bug | Open | A runner check on the run log fails now and then |

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

---

## JAP-5 — Bug — A job title wider than its column showed as nothing but `...`

**Status:** Fixed (v0.17.1). Reported 13 Nov 2026, seen on live.

Rows in the Jobs list whose title was wider than the Job title column drew the dot and then
`...` and nothing else — the title was gone, while the side panel showed it in full. Short
titles in the same list were fine, which made it look like bad data for particular jobs.

Not data: every `title_display` on live was clean (no leading whitespace, nothing empty, four
starting with punctuation and all of those deliberate, such as `.NET Engineer`). It was
layout.

The title is wrapped in a `<button>` so a row can be opened from the keyboard, and the global
rule `button, .btn { display: inline-flex }` makes that button an **atomic inline-level box**.
`text-overflow: ellipsis` on the surrounding `.t-title` can shorten *text* in a line box, but
it cannot cut into an atomic box: when the button did not fit, Chrome dropped the whole box
and painted the ellipsis in its place.

The unseen dot decides which of two wrong renderings you get, which is why it reproduced on
live and not at first locally. With the dot present something on the line fits, so Chrome
paints the ellipsis after it and the row reads `• ...`. With no dot the button is first on the
line and Chrome hard-clips it instead, cutting the title mid-word with no ellipsis. Both are
wrong; the dot only changes how.

Fixed in `styles.css` by making `.t-title` a flex line with `min-width: 0` and the button
`display: block` with its own `overflow: hidden; text-overflow: ellipsis`, so the button
shrinks to the column and shortens its own text. Checked at 700, 900, 1150, 1400 and 1800px
wide: the button never exceeds the cell and truncates only when it has to.

---

## JAP-6 — Bug — Settings you type saved only when the field lost focus

**Status:** Fixed (v0.18.1). Reported 13 Nov 2026, seen on live.

Settings says "Changes save straight away" at the top, and every radio and switch on the page
does exactly that. The four fields you *type* did not: they saved on blur only, so the way to
apply a number was to click somewhere else on the page. There was no Save button, and Enter —
the usual way to say "apply what I typed" — did nothing whatever.

Measured before the fix, typing in the monthly AI limit:

| action | PATCH /api/profile sent | Save button on page |
| --- | --- | --- |
| typed `7.5` | 0 | 0, none exists |
| pressed Enter | 0 | 0 |
| clicked outside | 1 | — |

The four: "Send at most N applications a day" and "Only jobs with at least N% of my required
skills" (both under JAP applies for me), the daily search time, and the monthly AI limit. Each
had its own copy of the same local-state-plus-onBlur code in `SettingsPage.tsx`.

Fixed with one small `Typed` component in that file, used by all four. Enter commits, so does
a Save button that appears beside the field the moment the value differs from the saved one,
Escape forgets the edit, and blur still commits so nothing typed is lost. The button suppresses
mousedown, or the blur would fire first and take the button away before the click landed. What
was typed stays on screen until the save returns, instead of flicking back to the old value in
between on a slow connection — the JAP-1 fault in miniature.

---

## JAP-7 — Bug — Every Ashby form read as "No application form found"

**Status:** Fixed (v0.18.2). Found 18 Nov 2026 on live, preparing two Supabase jobs.

Every Ashby application ended **Failed** with "No application form found on the page", and the
weekly form test had failed on Ashby three times in a row for the same reason. The screenshot
kept with the failure shows the job page with the Application tab open and only a spinner:
"Fetching application form".

Cause: Ashby's hosted page is a single-page app. It loads, the network goes quiet, and only
then does it fetch and draw the form. `_open_page` in `backend/pipeline/apply/form.py` waited
for `networkidle` and read the page straight away, so it saw no fields. Waiting for any input
was not enough either: Ashby attaches a hidden "autofill from resume" file input before the
form, and the rest of the fields render just after the first one.

Fixed in `_open_page`: after `networkidle` it waits up to 15 seconds (`FORM_WAIT_MS`) for a
*visible* form control that is not a file input (`FORM_CONTROL`), then one more second for the
remaining fields. A page with no form still fails as before, after that wait. Test:
`test_reads_a_form_drawn_after_the_network_goes_quiet` with `tests/forms/ashby_late.html`,
which draws its form two seconds after load. Checked against both live Supabase forms, which
now read all 11 fields.

---

## JAP-8 — Bug — Thanks already on the page counted as a confirmation

**Status:** Fixed (v0.18.3). Found 18 Nov 2026 on live, sending two Supabase (Ashby) jobs.

Both jobs became **Applied**, and JAP sent its own "Applied: ..." emails, with a screenshot that
showed the form still on the page and the Submit button spinning. No confirmation came from
Supabase or Ashby, so the applications most likely never went through: JAP had matched text
that was there before Submit was pressed.

Cause: `CONFIRMATION` in `backend/pipeline/apply/form.py` was searched in the whole page after
Submit, and Ashby shows a banner on every form, "Thanks for your interest and we appreciate
your understanding!", which matches. `_wait_for_answer` therefore returned at once, and
`submit_form` called it confirmed.

Fixed: `submit_form` reads the page before pressing Submit, and `confirmed_after` counts only
words of thanks that were not there before. `_wait_for_answer` waits for those new words
(`NEW_CONFIRMATION_JS`), so the screenshot is also taken after the site answers. A form that
leaves the page still counts as before. Tests: `test_thanks_already_on_the_page_is_no_confirmation`,
`test_ashby_yes_no_without_a_type_and_a_banner` (`tests/forms/ashby_yesno.html`).

---

## JAP-9 — Bug — Ashby Yes/No questions were not read

**Status:** Fixed (v0.18.3). Found 18 Nov 2026 on live, on the same two Supabase forms.

Supabase's form asks "Are you over the age of 18?" with Yes and No buttons, required. JAP
listed eleven fields and not this one, so it was sent unanswered.

Cause: `read_form.js` already reads questions made of buttons (the Linear case), but only
`button[type="button"]` whose box holds nothing but those buttons. Ashby's buttons here have
no `type` attribute, and the box also holds a hidden checkbox that keeps the value. The
required mark is a class on the question's title, with the `*` drawn by CSS.

Fixed: a button with no `type` counts as a plain button for this, hidden inputs beside the
buttons are allowed, and a title whose class says required makes the question required.
`BUTTONS_JS` clicks buttons with no `type` too. Checked against the live Supabase form, which
now reads twelve fields including this one as a required Yes/No.

---

## JAP-10 — Bug — Remote jobs failed Location when the model had not read them

**Status:** Fixed (v0.19.1). Found 18 Nov 2026 on live, with the OpenRouter key out of credit.

Remote jobs open to India were not listed for a profile matching on Remote regions = India.
With no extraction, a listing's work mode and remote regions stayed empty, and the Location
condition in `backend/pipeline/matching.py` needs the job to be remote and either a remote
region or a single location string carrying both "remote" and the region. Three shapes:

- Ashby with `isRemote: true` (or `workplaceType: Remote`) and location "India" (Atlan, AI
  Native Builder/Architect): saved as locations {India}, work mode empty.
- Greenhouse with separate "India" and "Remote" location entries (Together AI, Infra Agent
  Systems): neither entry has both words.
- Ashby with "remote-first" only in the description (Certa).

Cause: `direct_fields` in `backend/pipeline/enrich.py` already copied Ashby and Lever
`workplaceType`, but only into `posting.extracted`, which is written when the extraction call
succeeds; a failed call wrote nothing. It also ignored Ashby `isRemote` and Greenhouse's
"Remote" entries, and never gave remote regions.

Fixed: `backend/sources/workplace.py` reads the work mode the ATS states (Ashby and Lever
`workplaceType`, Ashby `isRemote`, a location entry saying remote) and, for a remote job, its
regions ("Remote - India" gives India; "India" beside "Remote", or with `isRemote`, gives
India; the office beside "Remote (US)" is not a region). `refresh_listings` in
`backend/pipeline/dedup.py` reads these from the stored payload on every refresh
(`work_place`), ahead of what the model says, so they count with no extraction at all.
Migration 0011 works them out once for the listings already stored, only adding (a value set
by hand is kept), and filters them again. Tests: `tests/test_workplace.py`,
`test_migration_fills_stated_work_mode`.

Not covered: a job that says remote only in its description (the Certa case) still needs the
model to read it.

---

## JAP-11 — Bug — Ashby applications were sent half filled

**Status:** Fixed (v0.19.2). Reported 18 Nov 2026 by Aditya from the screenshot of Atlan's Ashby
form (AI Native Builder); Certa's AI Engineer form failed the same way. JAP pressed Submit and
reported "Submitted, but the page did not confirm the application".

What the form held when Submit was pressed:

- Not filled: the SMS and WhatsApp opt-in Yes/No radios, the Location typeahead, "How did you
  learn about this opportunity?" and the acknowledgement checkbox.
- Wrong: "Are you currently eligible to work in the country you are applying for?" showed
  **No** (the answer was Yes), and "Will you require visa sponsorship?" was left empty (the
  answer was No).
- Phone without its country code (+91).
- A picture puzzle (CAPTCHA) on the form, untouched.

Causes, all in `backend/pipeline/apply/`:

1. Ashby marks a question required only by a class on its title (`_required_...`, the * drawn by
   CSS), with no `required` attribute on radios, checkboxes or the typeahead. `read_form.js`
   looked for that class only on button questions (JAP-9), so the rest read as optional, and
   `map_fields` fills an optional field only when the answer bank already has it. They went
   out empty, silently.
2. `BUTTONS_JS` clicked the first button with the answer's text whose ancestors, up to six
   levels, held the question's title. From the eligibility question's "No", six levels reach
   the box holding every question, visa included, so the visa answer "No" was clicked on the
   eligibility question, turning its Yes into No, and the visa question got nothing.
3. The Location typeahead lists places only after typing, so it read as a select with no
   options, and contact fields skipped selects: My profile's location was never used.
4. The phone came from My profile as saved, without a country code; Greenhouse has a separate
   country picker, Ashby does not.
5. The CAPTCHA check knew only reCAPTCHA's checkbox frame, hCaptcha and Turnstile, not a picture
   puzzle (a reCAPTCHA or hCaptcha challenge, Arkose/FunCaptcha, GeeTest), and nothing looked
   at the form again before pressing Submit.

Fixed:

- `read_form.js`: a field's title class marks it required (`markedRequired`); the question
  title of an Ashby field entry is its label; an input with `aria-autocomplete="list"` is a
  combobox; every field reports the value the page holds now; picture puzzles are detected,
  ignoring the challenge frame an invisible reCAPTCHA parks off screen.
- `BUTTONS_JS`: a button belongs to the nearest question title above it; walking up stops at
  the first box that holds any title.
- `fields.py`: a typeahead labelled as a location gets My profile's first location, and
  `form.py` `_combo` types it (then its first part, "Bengaluru") and picks the closest place.
  A phone on a form with no country picker gets the country code of where I live
  (`international`, `DIAL_CODES`).
- `form.py` `_check_before_submit`: just before Submit the form is read again. An empty
  required field (including one the first read missed), a choice showing another answer than
  JAP's, or a CAPTCHA stops it before Submit with the reason; nothing is sent.
- A CAPTCHA is never solved or worked around. The job stops at **Finish yourself: puzzle**
  (application at Needs input with only the puzzle open, listing `manual_apply`, in Review and
  Automatic mode). `make finish` (`infra/scripts/finish.py`, run on the Mac with uv) opens the
  same form filled in, in a visible browser; I solve it and press Submit, and it waits for the
  confirmation, keeps a screenshot and marks the job Applied
  (`python -m pipeline.apply.finish done`). The panel also has **I sent it**
  (`POST /api/applications/{id}/finished`). A puzzle that only appears after Submit holds the
  form, so that is not sent either and becomes Finish yourself.

Tests: `tests/forms/ashby_atlan.html`, a copy of Atlan's form with every field type, two
Yes/No button questions in a row, a typeahead that searches after typing, and `?captcha` /
`?challenge` puzzles. `test_reads_every_ashby_field_type`,
`test_ashby_form_is_filled_and_each_button_goes_to_its_question`,
`test_an_empty_required_field_stops_before_submit`,
`test_a_puzzle_stops_before_submit_and_is_never_touched`, `test_phone_gets_its_country_code`,
`test_a_puzzle_is_finish_yourself`, `test_a_puzzle_at_submit_is_finish_yourself`.

Not covered: the live Atlan form itself was not opened while fixing this (the build machine
could not reach jobs.ashbyhq.com). The fixture follows Ashby's markup as JAP-7 to JAP-9 saw
it; run `make form-check ENV=live` after merging to read a live Ashby form. Required
questions JAP has no saved answer for (SMS and WhatsApp opt-in, how you heard about the job,
the acknowledgement) now stop at Needs input once; the answers go to the answer bank and fill
every later Ashby form.

## JAP-12 — Bug — Applications waiting across the v0.23.0 deploy failed with "'int' is not iterable"

**Status:** Fixed (v0.24.1). Reported 19 Nov 2026 by Aditya ("a lot more applications are
failing to submit"). Three Supabase applications (SWE Storage, Turso, Spark) failed at 14:58 to
15:00 IST with `argument of type 'int' is not iterable`.

Cause: Inngest replays a function's finished steps from their stored results. These attempts
had passed `read-form` under v0.22.0, when `read_step` returned the field count as an int, and
then waited at Ready for review or Needs you. v0.23.0 (deployed 12:42 IST) made `read_step`
return a dict and added a board-hold loop that checks `"wait_until" not in read`
(`backend/functions/apply_listing.py`). When the waiting attempts resumed, the replayed int hit
that check, the TypeError failed the function three times, and the attempt became Failed. New
attempts were not affected.

Fixed: `form_was_read()` treats a result that is not a dict as "the form was read", so a run
that started on older code goes on as before. Retry each of the three on Jobs to apply again.

Lesson: a step's return type is part of the stored state of every run in flight. When a deploy
changes it, the new code must still accept the old shape, and a deploy should check for
waiting apply runs first.

## JAP-13 — Bug — Supabase silently dropped applications past its own limit

**Status:** Fixed (v0.25.0). Reported 19 Nov 2026 by Aditya: after the three JAP-12 retries,
only SWE Storage got a confirmation email. Turso and Spark failed with "Submitted, but the page
did not confirm the application".

Cause: Supabase's Ashby forms say "This job has application limits. You can submit up to 3
applications within a 60-day period". JAP had sent 3 on 18 Nov (AI Tooling, AI Platform
Engineer, AI Tooling Infrastructure). Storage went through as a 4th, then the form took
Turso's and Spark's submits and dropped them with no error on the page. JAP never read the
notice. Two smaller gaps let it happen fast: manual Retries skip the per-company daily limit
(that is a pick rule), and the per-ATS gap ran only from confirmed submits, so Spark went 26
seconds after the unconfirmed Turso.

Fixed: `read_form` reads a stated cap (`form.apply_limit`) and `read_and_save` keeps it on the
company (migration `0013_company_limit`). An attempt past the cap fails with "Company limit:
..., nothing was sent; it can try again from <date>", `submit` checks again just before
pressing (two jobs at one company can read their forms together), and `pick` passes over the
company until the window has room. The ATS and global gaps now count `submit_started_at` when
there is no `submitted_at`. Supabase takes nothing more from Aditya until about mid-January
2027; Turso and Spark were not sent and should not be retried before then.

Lesson: "did not confirm" can mean the employer refused silently. Read what the form says
about limits before sending, not only what it says after.

---

## JAP-14 — Bug — A runner check on the run log fails now and then

**Status:** Open. Found 24 Nov 2026 in CI, on `main`, right after pull request 37 (v0.27.0)
merged. Called a bug by Aditya the same day.

`make test-agent` (`agent/tests/run_test.sh`) has the check "run log: tokens per batch and a
run summary". It failed once, in the "agent runner" job of the push to `main` for commit
`e15aea9`, and passed in the six other CI runs of the same code that day (both branches, push
and pull request) and in every run on the Mac. Nothing else failed in that run: the checks on
the trace files beside it, "run trace: every session, with what was saved" among them, passed.
So the trace was right and the run log, the text the runner prints, was not what the check
expects.

The check greps the reader's output for two lines:

- a batch line, `saved 4 of 4 · ... 3 model requests · 2 tool calls · 6,300 tokens in, 21 out
  · fake-1`
- the summary line, `^[0-9:]* Trace: 9 sessions · `

Which of the two was missing is not known: CI keeps only the names of the checks, not the
run's output.

Root cause: not known. Things to look at first:

- Three reader shards run at once and each writes its lines through `log()` in `agent/run.sh`
  (`echo ... | tee -a "$LOG"`), to the same output and the same run log. Two lines written
  at the same moment could run together, and then neither matches a pattern anchored to a
  line.
- The batch line is put together from `trace.py session ... line`, which reads the session's
  files as soon as the import returns. A file not yet there would give a line without the
  tokens.
- Whether any batch is certain to hold exactly 4 postings that all save: the stand-in JAP
  (`agent/tests/fake-jap.sh`) hands 29 postings to 3 shards in batches of 4.

Fix: make the test keep the reader's output when a check fails (print it, so CI shows it),
find which line was wrong, then fix the cause: one writer for the run log, or lines written
whole. Until then a red "agent runner" job with only this check failing is this bug; re-run
it.

Files: `agent/tests/run_test.sh`, `agent/run.sh` (`log`, `reader_worker`), `agent/trace.py`
(`line_of`, `cmd_session`, `cmd_run`).
