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

