"""`make finish`: finish a job whose form has a picture puzzle, on this Mac (JAP-11).

JAP never solves or works around a CAPTCHA. It fills in everything else and stops before
Submit, and the job shows Finish yourself: puzzle. This opens the same form in a visible
Chromium window, filled in by the same code JAP submits with (backend/pipeline/apply/form.py),
then waits while you solve the puzzle and press Submit. When the page confirms the
application, it keeps a screenshot and marks the job Applied (and JAP sends the usual email).

    make finish                 the jobs waiting, or the only one
    make finish APP=<id>        that job

The form and answers come from the app container through a pipe and are never printed.
Runs on the Mac with `uv run --with playwright`, not in Docker: a container has no screen.
"""

import asyncio
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from playwright.async_api import Error, async_playwright  # noqa: E402

from pipeline.apply.form import (  # noqa: E402
    FormError,
    FormField,
    Upload,
    _anchor,
    _fill_all,
    _open_page,
    confirmed_after,
    selector,
)

WAIT_SECONDS = 20 * 60
DATA = ROOT / os.environ.get("JAP_DATA_HOST_DIR", "data")


def app_cli(*args: str) -> str:
    compose = shlex.split(os.environ["JAP_COMPOSE"])
    cmd = [*compose, "exec", "-T", "app", "python", "-m", "pipeline.apply.finish", *args]
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if done.returncode != 0:
        raise SystemExit(done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "failed")
    return done.stdout


def pick(arg: str | None) -> str:
    if arg:
        return arg
    jobs = json.loads(app_cli("list"))
    if not jobs:
        raise SystemExit("No job is waiting for you to finish it.")
    if len(jobs) == 1:
        return str(jobs[0]["id"])
    print("Jobs waiting for you to finish them (make finish APP=<id>):")
    for j in jobs:
        print(f"  {j['id']}  {j['company']}: {j['title']}")
    raise SystemExit(0)


async def finish(app_id: str) -> None:
    data = json.loads(app_cli("export", app_id))
    fields = [FormField(**f) for f in data["fields"]]
    answers: dict[str, str] = data["answers"]
    files = {
        k: Upload(DATA / v["path"], v["name"], v["mime"])
        for k, v in data["files"].items()
        if v.get("path")
    }
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()
        page = await _open_page(page, data["url"])
        try:
            await _fill_all(page, fields, answers, files)
        except FormError as exc:
            print(f"JAP could not fill everything ({exc}). Fill the rest by hand in the window.")
        before = await page.inner_text("body")
        print("The form is filled in. Solve the puzzle and press Submit in the browser window.")
        print("Waiting up to 20 minutes for the page to confirm the application...")
        anchor = _anchor(fields)
        confirmed = False
        for _ in range(WAIT_SECONDS // 2):
            if page.is_closed():
                break
            try:
                body = await page.inner_text("body")
                gone = anchor is not None and await page.locator(selector(anchor.key)).count() == 0
            except Error:
                await asyncio.sleep(2)  # the page is navigating after Submit
                continue
            if confirmed_after(before, body) or gone:
                confirmed = True
                break
            await asyncio.sleep(2)
        if not confirmed:
            print("No confirmation seen. If you did send it, press I sent it in the job's panel.")
            await browser.close()
            return
        shot = f"screenshots/{app_id}-finished.png"
        (DATA / "screenshots").mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(DATA / shot), full_page=True)
        await browser.close()
    app_cli("done", app_id, shot)
    print("Confirmed. The job is Applied, with a screenshot of the confirmation.")


if __name__ == "__main__":
    asyncio.run(finish(pick(sys.argv[1] if len(sys.argv) > 1 else None)))
