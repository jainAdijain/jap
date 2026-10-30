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
CONFIRMATION = re.compile(
    r"thank(s| you)[^.]{0,40}(appl|interest|submitt)|application (has been |was )?"
    r"(submitted|received|sent)|we(?:'ve| have) received your application",
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

# The text a combobox shows once an option is picked: the control around its input.
COMBO_SHOWN_JS = """(el) => {
  let p = el.parentElement;
  for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
    const t = (p.innerText || '').replace(/\\s+/g, ' ').trim();
    if (t) return t;
  }
  return el.value || '';
}"""
SELECTED_JS = "(el) => el.selectedIndex >= 0 ? [el.options[el.selectedIndex].text, el.value] : []"
# Fields the page marks invalid after Submit: the form refused it, so nothing was sent.
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
    if f.widget == "combobox":
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
    return bool(a) and (a == b or b.startswith(a) or b in a)  # maxlength may cut a long answer


async def _unfilled(
    page: Page, fields: list[FormField], answers: dict[str, str]
) -> list[FormField]:
    """The fields whose value on the page is not the one JAP put there."""
    out: list[FormField] = []
    for f in fields:
        value = answers.get(f.key, "")
        if not value or f.type in ("file", "radio", "checkbox", "checkboxes"):
            continue
        loc = page.locator(selector(f.key)).first
        try:
            if f.widget == "combobox":
                ok = _same(str(await loc.evaluate(COMBO_SHOWN_JS)), value)
            elif f.type == "select":
                picked = [str(x) for x in await loc.evaluate(SELECTED_JS)]
                ok = any(_same(p, value) for p in picked)
            else:
                ok = _same(await loc.input_value(), value)
        except Error:
            ok = False
        if not ok:
            out.append(f)
    return out


async def _fill_all(
    page: Page, fields: list[FormField], answers: dict[str, str], files: dict[str, Upload]
) -> None:
    """Fill every field, read the values back, fill again what didn't stick (a page that
    was still starting up clears it), and stop before Submit if anything is still empty."""
    if fields:
        await page.locator(selector(fields[0].key)).first.wait_for(state="attached")
    for f in fields:
        value = answers.get(f.key, "")
        if value or f.type == "file":
            await _fill(page, f, value, files)
    for _ in range(2):
        missing = await _unfilled(page, fields, answers)
        if not missing:
            return
        await page.wait_for_timeout(1_000)
        for f in missing:
            await _fill(page, f, answers[f.key], files)
    missing = await _unfilled(page, fields, answers)
    if missing:
        labels = "; ".join(f.label for f in missing)
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
            form_left = bool(fields) and await page.locator(selector(fields[0].key)).count() == 0
            await page.screenshot(path=str(screenshot), full_page=True)
            shot = True
            if CONFIRMATION.search(body) or form_left:
                return SubmitResult(confirmed=True, screenshot=True, clicked=True)
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
            return SubmitResult(
                confirmed=False,
                screenshot=True,
                clicked=True,
                error="Submitted, but the page did not confirm the application",
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
