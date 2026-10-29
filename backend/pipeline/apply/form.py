"""The browser side of applying: read a public application form, and fill and submit it.

Greenhouse, Lever and Ashby forms differ in markup, so the reader works from what every
form has: inputs, text areas and selects with a label, a required flag (the required
attribute, aria-required, or a trailing *), and a submit button. Each call opens a fresh
browser, so no session outlives a step (no browser survives a wait, design doc Apply).

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


@dataclass
class FormField:
    key: str
    label: str
    type: str  # text, email, tel, url, number, date, textarea, select, radio, checkbox(es), file
    required: bool
    options: list[str] = field(default_factory=list)
    max_chars: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "options": self.options,
            "max_chars": self.max_chars,
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


async def _fill(page: Page, f: FormField, value: str, files: dict[str, Upload]) -> None:
    loc = page.locator(selector(f.key)).first
    if f.type == "file":
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
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            if resp is not None and resp.status >= 400:
                raise FormError(f"The form page answered {resp.status}")
            for f in fields:
                value = answers.get(f.key, "")
                if value or f.type == "file":
                    await _fill(page, f, value, files)
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
