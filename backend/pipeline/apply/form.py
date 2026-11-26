"""The browser side of applying: read a public application form, and fill and submit it.

Greenhouse, Lever and Ashby forms differ in markup, so the reader works from what every
form has: inputs, text areas and selects with a label, a required flag (the required
attribute, aria-required, or a trailing *), and a submit button. Each call opens a fresh
browser, so no session outlives a step (no browser survives a wait, design doc Apply).

A dropdown built from a text input (role="combobox", as on Greenhouse's job-boards pages)
is read as a select: its options are listed by opening it, and it is filled by picking the
option. Newer boards render the form with JavaScript and can clear what was typed before
the page settled, so submit waits for the page to settle, reads every value back, fills
again what didn't stick, and never presses Submit while a field is still empty.

No model is involved here: which fields are required comes from the page itself.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error, FilePayload, Page, async_playwright

NAV_TIMEOUT_MS = 30_000
SETTLE_TIMEOUT_MS = 15_000
FORM_WAIT_MS = 15_000  # how long a form drawn after load (Ashby) may take to appear
FORM_CONTROL = "input:not([type=hidden]):not([type=file]), textarea, select, [role=combobox]"
RECHECK_MS = 1_000
CONFIRMATION = re.compile(
    r"thank(s| you)[^.]{0,40}(appl|interest|submitt)|application (has been |was )?"
    r"(successfully )?(submitted|received|sent)|we(?:'ve| have) received your application"
    r"|successfully (submitted|applied)",
    re.I,
)

# Runs in the page: every field with its label, type and required flag (read_form.js).
READ_JS = (Path(__file__).parent / "read_form.js").read_text()

OPTION_LABEL_JS = """(o) => {
  const l = (o.id && document.querySelector(`label[for="${CSS.escape(o.id)}"]`))
    || o.closest('label');
  return ((l && (l.innerText || l.textContent)) || o.value).replace(/\\s+/g, ' ').trim();
}"""
REQUIRED_MARK = re.compile(r"\s*([*✱★]|\(required\)|required)\s*$", re.I)

# What a combobox shows: the text of the control around its input, and any hidden input
# near it that holds the picked value. A pick shows as a change from the fresh page.
COMBO_STATE_JS = """(el) => {
  let shown = '', p = el.parentElement, top = p;
  for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
    const t = (p.innerText || '').replace(/\\s+/g, ' ').trim();
    if (t && !shown) shown = t;
    top = p;
  }
  const hidden = [...(top ? top.querySelectorAll('input[type=hidden]') : [])]
    .map((h) => h.value).filter(Boolean).join(',');
  return [shown, hidden];
}"""
SELECTED_JS = "(el) => el.selectedIndex >= 0 ? [el.options[el.selectedIndex].text, el.value] : []"
# Fields the page marks invalid after Submit: the form refused it, so nothing was sent.
# Error text the page shows after Submit (a banner, an alert, a field message).
ERRORS_JS = """() => [...document.querySelectorAll(
    '[role="alert"], [aria-live="assertive"], [class*="error" i], [class*="invalid" i]')]
  .filter((el) => el.offsetParent !== null && !el.querySelector('[role="alert"]'))
  .map((el) => (el.innerText || '').replace(/\\s+/g, ' ').trim()).filter(Boolean)"""
# Click the button whose text is the answer, in the button group under the question
# (read_form.js keys such a field "buttons:<question>"). True when one was clicked. A button
# belongs to the nearest question title above it: walking up from a "No" button stops at the
# first box that holds any title, so the "No" of one question is never taken for the next
# question's "No" (JAP-11: "eligible to work" was set to No by the visa question's answer).
BUTTONS_JS = """([question, answer]) => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const strip = (s) => clean(s).replace(/\\s*[*✱★]\\s*$/, '');
  const q = strip(question);
  const TITLES = 'label, legend, [class*="title" i], [class*="label" i]';
  for (const b of document.querySelectorAll('button[type="button"], button:not([type])')) {
    if (clean(b.innerText) !== clean(answer)) continue;
    let p = b.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const titles = [...p.querySelectorAll(TITLES)]
        .filter((l) => !l.contains(b) && !l.querySelector('button') && strip(l.innerText));
      if (!titles.length) continue;
      if (titles.some((l) => strip(l.innerText) === q)) {
        b.click();
        return true;
      }
      break;  // the nearest question is another one
    }
  }
  return false;
}"""
# Wait for words of thanks the page did not show before Submit (JAP-8)
NEW_CONFIRMATION_JS = """([re, before]) =>
  (document.body.innerText.match(new RegExp(re, 'gi')) || [])
    .some((m) => !before.includes(m.toLowerCase()))"""
# Greenhouse's check for automated browsers: after Submit it emails a security code and
# shows boxes for it. Nothing is sent until the code is typed in.
SECURITY_CODE = re.compile(r"(security|verification) code|enter the \d+-character code", re.I)
CODE_LENGTH = re.compile(r"(\d+)[- ]character code", re.I)
# (when Submit was pressed, code length) -> the emailed code, or None (A5)
GetCode = Callable[[datetime, int], Awaitable[str | None]]
CODE_INPUT = re.compile(r"code|otp|verification|security", re.I)
# A CAPTCHA a person must solve (JAP-11). JAP never tries to solve or get around one: it
# stops before Submit with every field filled, and I finish the form myself (make finish).
PUZZLE_FOUND = "Finish yourself: puzzle"
# A picture puzzle shown after Submit (an invisible reCAPTCHA or hCaptcha escalating).
CHALLENGE_JS = """() => [...document.querySelectorAll(
    'iframe[src*="recaptcha"][src*="bframe"], iframe[title*="recaptcha challenge" i],'
    + ' iframe[src*="hcaptcha"][src*="challenge"], iframe[src*="arkoselabs"],'
    + ' iframe[src*="funcaptcha"], iframe[src*="geetest"], iframe[title*="captcha" i],'
    + ' iframe[title*="verification challenge" i]')]
  .some((n) => { const s = getComputedStyle(n), r = n.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 1 && r.height > 1
      && r.bottom + window.scrollY > 0 && r.right + window.scrollX > 0; })"""
# Words in such text that mean the employer's site turned the submission away.
NOT_SENT = re.compile(
    r"spam|captcha|robot|automated|try again|could not be submitted|failed|required|missing",
    re.I,
)
INVALID_JS = """() => [...document.querySelectorAll('[aria-invalid="true"], :invalid')]
  .filter((el) => ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName))
  .map((el) => el.name || el.id).filter(Boolean)"""


@dataclass
class FormField:
    key: str
    label: str
    type: str  # text, email, tel, url, number, date, textarea, select, radio, checkbox(es), file
    required: bool
    options: list[str] = field(default_factory=list)
    max_chars: int | None = None
    widget: str = ""  # "combobox": a select built from a text input

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "options": self.options,
            "max_chars": self.max_chars,
            "widget": self.widget,
        }


@dataclass
class FormRead:
    fields: list[FormField]
    captcha: bool
    # The employer's own cap on applications from one person, as (how many, in how many
    # days), when the form states one (JAP-13). None when it does not.
    limit: tuple[int, int] | None = None


# Supabase on Ashby: "You can submit up to 3 applications within a 60-day period".
APPLY_LIMIT = re.compile(
    r"(?:submit|send|apply)(?: for)? up to (\d+) (?:applications?|jobs?|roles?|positions?)"
    r"(?: in total)? (?:with)?in (?:a |an |any |the )?(?:(?:rolling|last|past) )?"
    r"(\d+)[- ]?(?:days?|-day period)",
    re.IGNORECASE,
)


def apply_limit(text: str) -> tuple[int, int] | None:
    """The (applications, days) cap a form states, or None."""
    m = APPLY_LIMIT.search(" ".join(text.split()))
    if m is None or int(m.group(1)) < 1 or int(m.group(2)) < 1:
        return None
    return int(m.group(1)), int(m.group(2))


@dataclass
class SubmitResult:
    confirmed: bool
    screenshot: bool
    clicked: bool  # Submit was pressed: never press it again for this attempt
    error: str | None = None
    # Pressed, but the form marked fields invalid and stayed: nothing was sent
    rejected: bool = False
    # The site emailed a security code and waits for it: nothing was sent
    needs_code: bool = False
    # The form shows a CAPTCHA (before or after Submit): nothing was sent, skip the job
    captcha: bool = False
    # The site the page came from at the end, after any redirect: who refused, if one did (V5)
    host: str | None = None


class FormError(Exception):
    """The form could not be opened, read or filled (a site change or a timeout). `host` is
    the site whose page answered, when it refused JAP (V5: the board, or the company)."""

    def __init__(self, message: str, host: str | None = None) -> None:
        super().__init__(message)
        self.host = host


def host_of(url: str | None) -> str | None:
    return urlsplit(url or "").hostname or None


class CaptchaFound(FormError):
    """A CAPTCHA is on the form before Submit: I finish the form myself, JAP never solves it."""


def to_fields(raw: list[dict[str, Any]]) -> list[FormField]:
    out: list[FormField] = []
    for r in raw:
        label = str(r.get("label") or r["key"])
        required = bool(r.get("required"))
        if REQUIRED_MARK.search(label):
            required = True
            label = REQUIRED_MARK.sub("", label)
        out.append(
            FormField(
                key=str(r["key"]),
                label=label.strip() or str(r["key"]),
                type=str(r["type"]),
                required=required,
                options=[str(o) for o in r.get("options") or [] if str(o).strip()],
                max_chars=r.get("max_chars"),
                widget=str(r.get("widget") or ""),
            )
        )
    return out


async def _open_page(page: Page, url: str) -> Page:
    page.set_default_timeout(SETTLE_TIMEOUT_MS)
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    if resp is not None and resp.status >= 400:
        raise FormError(f"The form page answered {resp.status}", host=host_of(resp.url))
    try:
        await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
    except Error:
        pass  # some boards keep a connection open; the form is there by now
    try:
        # Ashby's hosted page says "Fetching application form" and draws the fields a
        # moment after the network goes quiet, so a quiet network is not a loaded form
        # (JAP-7). A page with no form at all just waits this out and is read as it is.
        # Visible, and not a file input: Ashby attaches its hidden "autofill from resume"
        # file input before the form itself.
        await page.wait_for_selector(FORM_CONTROL, state="visible", timeout=FORM_WAIT_MS)
        await page.wait_for_timeout(RECHECK_MS)  # the rest of the fields render just after
    except Error:
        pass
    return page


async def read_form(url: str, screenshot: Path | None = None) -> FormRead:
    """Every field on the form. When the form can't be read, the page as it was is kept at
    `screenshot` (when given) so a Failed job shows what JAP saw."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page: Page | None = None
        try:
            page = await browser.new_page()
            try:
                page = await _open_page(page, url)
                data = await page.evaluate(READ_JS)
                limit = apply_limit(await page.inner_text("body"))
                for r in data["fields"]:
                    if r.get("widget") == "combobox":
                        r["options"] = await _combo_options(page, str(r["key"]))
            except Error as exc:
                raise FormError(f"Could not read the form: {exc.message.splitlines()[0]}") from exc
            fields = to_fields(data["fields"])
            if not fields:
                raise FormError("No application form found on the page")
        except FormError:
            if screenshot is not None and page is not None:
                await _shot(page, screenshot)
            raise
        finally:
            await browser.close()
    return FormRead(fields=fields, captcha=bool(data["captcha"]), limit=limit)


async def _shot(page: Page, path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=True)
    except Error:
        return False
    return True


async def _combo_options(page: Page, key: str) -> list[str]:
    """Open a combobox, list its options, close it again."""
    loc = page.locator(selector(key)).first
    try:
        await loc.click()
        options = page.get_by_role("option")
        await options.first.wait_for(timeout=3_000)
        texts = await options.all_inner_texts()
    except Error:
        texts = []
    try:
        await page.keyboard.press("Escape")
    except Error:
        pass
    return [t for t in (" ".join(x.split()) for x in texts) if t]


# Ashby names each radio group anew on every page load: a random id, then the question's
# own name ("<uuid>_question_17759507004"). JAP reads the form on one load and submits it
# on another, so such a key is matched by what follows the id (JAP-16).
LOAD_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_(?=.)", re.I)


def stable_key(key: str) -> str:
    return LOAD_ID.sub("", key)


def selector(key: str) -> str:
    if key.startswith("id:"):
        return f'[id="{key[3:]}"]'
    if (rest := stable_key(key)) != key:
        return f'[name$="_{rest}"]'
    return f'[name="{key}"]'


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("yes", "true", "1", "on", "checked", "i agree", "agree")


async def _choose(page: Page, f: FormField, value: str) -> None:
    """Tick the radio or checkbox option(s) whose label is the value."""
    wanted = (
        {v.strip().lower() for v in value.split(";")}
        if f.type == "checkboxes"
        else {value.strip().lower()}
    )
    kind = "radio" if f.type == "radio" else "checkbox"
    inputs = page.locator(f'input[type="{kind}"]{selector(f.key)}')
    count = await inputs.count()
    if f.type == "checkbox" and count == 1:
        if _truthy(value):
            await inputs.first.check()
        return
    hit = False
    for i in range(count):
        option = inputs.nth(i)
        label = await option.evaluate(OPTION_LABEL_JS)
        if str(label).strip().lower() in wanted:
            await option.check()
            hit = True
    if not hit and f.required:
        raise FormError(f'No option "{value}" for "{f.label}"')


@dataclass
class Upload:
    path: Path
    name: str  # the file name the employer sees (the resume's original name)
    mime: str


def _best_option(texts: list[str], value: str) -> int | None:
    """The option a typeahead offers for a value: the same text, else one holding all of
    it, else one holding its first part ("Bengaluru, India" picks "Bengaluru, Karnataka,
    India"), preferring one that also names its other parts."""
    norm = [" ".join(t.split()).lower() for t in texts]
    want = " ".join(value.split()).lower()
    for i, t in enumerate(norm):
        if t == want:
            return i
    for i, t in enumerate(norm):
        if want in t:
            return i
    parts = [p.strip() for p in want.split(",") if p.strip()]
    if not parts:
        return None
    hits = [i for i, t in enumerate(norm) if parts[0] in t]
    if not hits:
        return None
    return max(hits, key=lambda i: sum(p in norm[i] for p in parts[1:]))


async def _combo(page: Page, f: FormField, value: str) -> None:
    """Pick the option of a combobox: type to filter it, then click it. A typeahead (Ashby's
    Location) lists places only after typing and may not know the whole value, so when
    nothing fits it types the first part ("Bengaluru") and picks the closest place."""
    loc = page.locator(selector(f.key)).first
    for typed in dict.fromkeys([value, value.split(",")[0].strip()]):
        if not typed:
            continue
        await loc.click()
        await loc.fill(typed)
        options = page.get_by_role("option")
        try:
            await options.first.wait_for(timeout=3_000)
        except Error:
            continue
        await page.wait_for_timeout(300)  # a typeahead may still be narrowing its list
        texts = await options.all_inner_texts()
        best = _best_option(texts, value)
        if best is not None:
            await options.nth(best).click()
            return
    try:
        await page.keyboard.press("Escape")
    except Error:
        pass
    raise FormError(f'No option "{value}" for "{f.label}"')


async def _fill(page: Page, f: FormField, value: str, files: dict[str, Upload]) -> None:
    loc = page.locator(selector(f.key)).first
    if f.widget == "buttons":
        if value and not await page.evaluate(BUTTONS_JS, [f.label, value]):
            raise FormError(f'No button "{value}" for "{f.label}"')
    elif f.widget == "combobox":
        await _combo(page, f, value)
    elif f.type == "file":
        if f.key in files:
            up = files[f.key]
            data = await asyncio.to_thread(up.path.read_bytes)
            payload: FilePayload = {"name": up.name, "mimeType": up.mime, "buffer": data}
            await loc.set_input_files(payload)
    elif f.type == "select":
        try:
            await loc.select_option(label=value)
        except Error:
            await loc.select_option(value=value)
    elif f.type in ("radio", "checkbox", "checkboxes"):
        await _choose(page, f, value)
    elif value:
        await loc.fill(value)


def _same(shown: str, value: str) -> bool:
    a, b = " ".join(shown.split()).lower(), " ".join(value.split()).lower()
    if a and (a == b or b.startswith(a) or b in a):  # maxlength may cut a long answer
        return True
    da, db = re.sub(r"\D", "", a), re.sub(r"\D", "", b)  # a phone the page reformatted
    return len(da) >= 7 and len(db) >= 7 and da[-10:] == db[-10:]


def _anchor(fields: list[FormField]) -> FormField | None:
    """A field with an element of its own, to tell whether the form is on the page."""
    return next((f for f in fields if f.widget != "buttons"), None)


async def _combo_state(page: Page, f: FormField) -> tuple[str, str]:
    shown, hidden = await page.locator(selector(f.key)).first.evaluate(COMBO_STATE_JS)
    return str(shown), str(hidden)


async def _unfilled(
    page: Page,
    fields: list[FormField],
    answers: dict[str, str],
    fresh: dict[str, tuple[str, str]],
) -> list[tuple[FormField, str]]:
    """The fields whose value on the page is not the one JAP put there, with what the
    page shows instead. A combobox counts as filled when it shows the answer, or when it
    changed from how the fresh page showed it (a country picker may show only "+91")."""
    out: list[tuple[FormField, str]] = []
    for f in fields:
        value = answers.get(f.key, "")
        if not value or f.type in ("file", "radio", "checkbox", "checkboxes"):
            continue  # a button group is radio too: clicking it is all JAP can check
        loc = page.locator(selector(f.key)).first
        shown = ""
        try:
            if f.widget == "combobox":
                state = await _combo_state(page, f)
                shown = state[0]
                ok = _same(shown, value) or (any(state) and state != fresh.get(f.key))
            elif f.type == "select":
                picked = [str(x) for x in await loc.evaluate(SELECTED_JS)]
                shown = picked[0] if picked else ""
                ok = any(_same(p, value) for p in picked)
            else:
                shown = await loc.input_value()
                ok = _same(shown, value)
        except Error:
            ok = False
        if not ok:
            out.append((f, shown))
    return out


async def _fill_all(
    page: Page, fields: list[FormField], answers: dict[str, str], files: dict[str, Upload]
) -> None:
    """Fill every field, read the values back, fill again what didn't stick (a page that
    was still starting up clears it), and stop before Submit if anything is still empty."""
    if anchor := _anchor(fields):
        await page.locator(selector(anchor.key)).first.wait_for(state="attached")
    fresh = {
        f.key: await _combo_state(page, f)
        for f in fields
        if f.widget == "combobox" and answers.get(f.key)
    }
    for f in fields:
        value = answers.get(f.key, "")
        if value or f.type == "file":
            await _fill(page, f, value, files)
    for _ in range(2):
        await page.wait_for_timeout(RECHECK_MS)  # a re-render after typing clears fields
        missing = await _unfilled(page, fields, answers, fresh)
        if not missing:
            return
        for f, _shown in missing:
            await _fill(page, f, answers[f.key], files)
    await page.wait_for_timeout(RECHECK_MS)
    missing = await _unfilled(page, fields, answers, fresh)
    if missing:
        labels = "; ".join(
            f'{f.label} (wanted "{answers[f.key][:40]}", the page shows "{shown[:40]}")'
            for f, shown in missing
        )
        raise FormError(f"These fields would not keep their value, so nothing was sent: {labels}")


def _picked(value: str, answer: str) -> bool:
    got = {" ".join(v.split()).lower() for v in value.split(";") if v.strip()}
    want = {" ".join(v.split()).lower() for v in answer.split(";") if v.strip()}
    return bool(got) and (got == want or all(any(g.startswith(w) for g in got) for w in want))


async def _check_before_submit(
    page: Page, fields: list[FormField], answers: dict[str, str]
) -> None:
    """Read the form again as it is now, just before Submit (JAP-11). Stop with the reason
    when a CAPTCHA has appeared, when a required field is empty (including one the first
    read missed), or when a choice shows another answer than JAP's (a button group that
    ended up "No" when the answer was "Yes"). A field whose value the page doesn't show
    (a combobox, or buttons with no pressed state) was already checked by _fill_all."""
    data = await page.evaluate(READ_JS)
    if data["captcha"]:
        raise CaptchaFound(PUZZLE_FOUND)
    known = {stable_key(f.key): f for f in fields}
    empty: list[str] = []
    wrong: list[str] = []
    for r in data["fields"]:
        key, value = str(r["key"]), r.get("value")
        f = known.get(stable_key(key))
        required = bool(r.get("required")) or bool(REQUIRED_MARK.search(str(r.get("label") or "")))
        required = required or (f is not None and f.required)
        label = REQUIRED_MARK.sub("", str(r.get("label") or key)).strip() or key
        answer = answers.get(f.key if f else key, "")
        if value is None:
            if required and not answer and not (f and f.type == "file"):
                empty.append(label)
            continue
        value = str(value)
        if required and not value.strip():
            if r["type"] == "checkbox" and answer and not _truthy(answer):
                continue  # a box I said no to stays unticked; the page will say if it minds
            empty.append(label)
        elif answer and r["type"] in ("radio", "checkboxes", "select") and value.strip():
            if not _picked(value, answer):
                wrong.append(f'{label} (wanted "{answer[:40]}", the page shows "{value[:40]}")')
    problems = []
    if empty:
        problems.append("required fields are still empty: " + "; ".join(dict.fromkeys(empty)))
    if wrong:
        problems.append("these show another answer: " + "; ".join(dict.fromkeys(wrong)))
    if problems:
        raise FormError("Stopped before Submit, so nothing was sent: " + ", and ".join(problems))


def _confirmations(text: str) -> set[str]:
    return {m.group(0).lower() for m in CONFIRMATION.finditer(text)}


def confirmed_after(before: str, after: str) -> bool:
    """The page thanks me for applying in words it did not show before Submit. Ashby
    shows "Thanks for your interest" in a banner on every form, so text that was already
    there is no confirmation (JAP-8)."""
    return bool(_confirmations(after) - _confirmations(before))


async def _wait_for_answer(page: Page, before: str) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
    except Error:
        pass
    try:
        await page.wait_for_function(
            NEW_CONFIRMATION_JS,
            arg=[CONFIRMATION.pattern, sorted(_confirmations(before))],
            timeout=SETTLE_TIMEOUT_MS,
        )
    except Error:
        pass


async def _enter_code(page: Page, code: str) -> bool:
    """Type the code into the boxes the site shows: one box per character, or one field
    named for the code. False when there is nowhere to type it."""
    boxes = page.locator('input[maxlength="1"]:visible')
    if await boxes.count() >= len(code):
        for i, ch in enumerate(code):
            await boxes.nth(i).fill(ch)
        return True
    inputs = page.locator("input:visible")
    for i in range(await inputs.count()):
        box = inputs.nth(i)
        names = " ".join(
            [await box.get_attribute(a) or "" for a in ("name", "id", "aria-label", "autocomplete")]
        )
        if CODE_INPUT.search(names) and not await box.input_value():
            await box.fill(code)
            return True
    return False


async def submit_form(
    url: str,
    fields: list[FormField],
    answers: dict[str, str],
    files: dict[str, Upload],
    screenshot: Path,
    trace: Path,
    get_code: GetCode | None = None,
) -> SubmitResult:
    """Open the form fresh, fill the stored answers, attach files, submit, and screenshot
    whatever page comes back. Confirmed only when the page says the application was
    received (or the form is gone). When the site asks for a code it emailed and get_code
    is given, the code is typed in and Submit pressed once more (A5)."""
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    trace.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        await context.tracing.start(screenshots=True, snapshots=True)
        page = await context.new_page()
        page.set_default_timeout(SETTLE_TIMEOUT_MS)
        shot = clicked = False
        try:
            page = await _open_page(page, url)
            await _fill_all(page, fields, answers, files)
            await _check_before_submit(page, fields, answers)
            button = page.locator(
                'form button[type="submit"], form input[type="submit"], button[type="submit"]'
            )
            if await button.count() == 0:
                button = page.get_by_role("button", name=re.compile("submit|apply", re.I))
            if await button.count() == 0:
                raise FormError("No submit button on the form")
            before = await page.inner_text("body")
            clicked = True
            pressed_at = datetime.now(UTC)
            await button.first.click()
            await _wait_for_answer(page, before)
            body = await page.inner_text("body")
            code_error = None
            if (
                get_code is not None
                and SECURITY_CODE.search(body)
                and not confirmed_after(before, body)
            ):
                length = int(m.group(1)) if (m := CODE_LENGTH.search(body)) else 8
                code = await get_code(pressed_at, length)
                if code is None:
                    code_error = "No security code reached JAP's mailbox in 3 minutes"
                elif not await _enter_code(page, code):
                    code_error = "JAP had the security code but found no boxes to type it in"
                else:
                    await button.first.click()  # the one second press, only after a code
                    await _wait_for_answer(page, before)
                    body = await page.inner_text("body")
                    code_error = "The site did not take the security code"
            anchor = _anchor(fields)
            form_left = anchor is not None and await page.locator(selector(anchor.key)).count() == 0
            await page.screenshot(path=str(screenshot), full_page=True)
            shot = True
            if confirmed_after(before, body) or form_left:
                return SubmitResult(confirmed=True, screenshot=True, clicked=True)
            if await page.evaluate(CHALLENGE_JS):  # a picture puzzle holds the submission
                return SubmitResult(
                    confirmed=False,
                    screenshot=True,
                    clicked=True,
                    rejected=True,
                    captcha=True,
                    error=PUZZLE_FOUND + ": the site showed a picture puzzle after Submit, "
                    "which JAP never solves, so nothing was sent",
                )
            if SECURITY_CODE.search(body):
                return SubmitResult(
                    confirmed=False,
                    screenshot=True,
                    clicked=True,
                    rejected=True,
                    needs_code=True,
                    error=(code_error + ", so nothing was sent. Apply yourself from the posting")
                    if code_error
                    else "The site emailed you a security code and wants it before it takes "
                    "the application, so nothing was sent. Apply yourself from the posting",
                )
            keys = {stable_key(f.key.removeprefix("id:")): f.label for f in fields}
            marked = [stable_key(k) for k in await page.evaluate(INVALID_JS)]
            invalid = [keys[k] for k in marked if k in keys]
            if invalid:
                return SubmitResult(
                    confirmed=False,
                    screenshot=True,
                    clicked=True,
                    rejected=True,
                    error="The form refused the submission, so nothing was sent. It marked: "
                    + "; ".join(dict.fromkeys(invalid)),
                )
            said = "; ".join(dict.fromkeys(await page.evaluate(ERRORS_JS)))[:300]
            if said and NOT_SENT.search(said):
                return SubmitResult(
                    confirmed=False,
                    screenshot=True,
                    clicked=True,
                    rejected=True,
                    error=f'The employer\'s site turned the submission away: "{said}"',
                    host=host_of(page.url),
                )
            return SubmitResult(
                confirmed=False,
                screenshot=True,
                clicked=True,
                error="Submitted, but the page did not confirm the application"
                + (f'. It says: "{said}"' if said else ""),
            )
        except (Error, FormError) as exc:
            if not shot:
                try:
                    await page.screenshot(path=str(screenshot), full_page=True)
                    shot = True
                except Error:
                    pass
            message = exc.message.splitlines()[0] if isinstance(exc, Error) else str(exc)
            return SubmitResult(
                confirmed=False,
                screenshot=shot,
                clicked=clicked,
                error=message,
                captcha=isinstance(exc, CaptchaFound),
                host=(exc.host if isinstance(exc, FormError) else None) or host_of(page.url),
            )
        finally:
            try:
                await context.tracing.stop(path=str(trace))
            finally:
                await browser.close()
