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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import Error, FilePayload, Page, async_playwright

NAV_TIMEOUT_MS = 30_000
SETTLE_TIMEOUT_MS = 15_000
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
# (read_form.js keys such a field "buttons:<question>"). True when one was clicked.
BUTTONS_JS = """([question, answer]) => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const strip = (s) => clean(s).replace(/\\s*[*✱★]\\s*$/, '');
  for (const b of document.querySelectorAll('button[type="button"]')) {
    if (clean(b.innerText) !== clean(answer)) continue;
    let p = b.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const titles = p.querySelectorAll('label, legend, [class*="title" i], [class*="label" i]');
      const q = strip(question);
      if ([...titles].some((l) => !l.contains(b) && strip(l.innerText) === q)) {
        b.click();
        return true;
      }
    }
  }
  return false;
}"""
# Greenhouse's check for automated browsers: after Submit it emails a security code and
# shows boxes for it. Nothing is sent until the code is typed in.
SECURITY_CODE = re.compile(r"(security|verification) code|enter the \d+-character code", re.I)
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


class FormError(Exception):
    """The form could not be opened, read or filled (a site change or a timeout)."""


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
        raise FormError(f"The form page answered {resp.status}")
    try:
        await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
    except Error:
        pass  # some boards keep a connection open; the form is there by now
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
    return FormRead(fields=fields, captcha=bool(data["captcha"]))


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


def selector(key: str) -> str:
    if key.startswith("id:"):
        return f'[id="{key[3:]}"]'
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


async def _combo(page: Page, f: FormField, value: str) -> None:
    """Pick the option of a combobox: type to filter it, then click it."""
    loc = page.locator(selector(f.key)).first
    await loc.click()
    await loc.fill(value)
    option = page.get_by_role("option", name=value, exact=True)
    try:
        await option.first.wait_for(timeout=3_000)
    except Error:
        option = page.get_by_role("option", name=re.compile(re.escape(value), re.I))
        if await option.count() == 0:
            await page.keyboard.press("Escape")
            raise FormError(f'No option "{value}" for "{f.label}"') from None
    await option.first.click()


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


async def submit_form(
    url: str,
    fields: list[FormField],
    answers: dict[str, str],
    files: dict[str, Upload],
    screenshot: Path,
    trace: Path,
) -> SubmitResult:
    """Open the form fresh, fill the stored answers, attach files, submit, and screenshot
    whatever page comes back. Confirmed only when the page says the application was
    received (or the form is gone)."""
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
            button = page.locator(
                'form button[type="submit"], form input[type="submit"], button[type="submit"]'
            )
            if await button.count() == 0:
                button = page.get_by_role("button", name=re.compile("submit|apply", re.I))
            if await button.count() == 0:
                raise FormError("No submit button on the form")
            clicked = True
            await button.first.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
            except Error:
                pass
            try:
                await page.wait_for_function(
                    "(re) => new RegExp(re, 'i').test(document.body.innerText)",
                    arg=CONFIRMATION.pattern,
                    timeout=SETTLE_TIMEOUT_MS,
                )
            except Error:
                pass
            body = await page.inner_text("body")
            anchor = _anchor(fields)
            form_left = anchor is not None and await page.locator(selector(anchor.key)).count() == 0
            await page.screenshot(path=str(screenshot), full_page=True)
            shot = True
            if CONFIRMATION.search(body) or form_left:
                return SubmitResult(confirmed=True, screenshot=True, clicked=True)
            if SECURITY_CODE.search(body):
                return SubmitResult(
                    confirmed=False,
                    screenshot=True,
                    clicked=True,
                    rejected=True,
                    needs_code=True,
                    error="The site emailed you a security code and wants it before it takes "
                    "the application, so nothing was sent. Apply yourself from the posting",
                )
            keys = {f.key.removeprefix("id:"): f.label for f in fields}
            invalid = [keys[k] for k in await page.evaluate(INVALID_JS) if k in keys]
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
            return SubmitResult(confirmed=False, screenshot=shot, clicked=clicked, error=message)
        finally:
            try:
                await context.tracing.stop(path=str(trace))
            finally:
                await browser.close()
