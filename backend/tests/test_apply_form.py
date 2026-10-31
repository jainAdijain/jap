"""The form reader and filler against local copies of Greenhouse-, Lever- and Ashby-style
forms, in a real headless Chromium. Skipped when Chromium is not installed (CI installs it).
"""

import threading
import time
from collections.abc import Iterator
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from pipeline.apply import fields as rules
from pipeline.apply.form import (
    CONFIRMATION,
    FormError,
    FormField,
    Upload,
    read_form,
    submit_form,
    to_fields,
)

FORMS = Path(__file__).parent / "forms"


def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001 - any failure means no browser here
        return False


needs_browser = pytest.mark.skipif(not _chromium_ok(), reason="Chromium is not installed")


class _Handler(BaseHTTPRequestHandler):
    posts: list[dict[str, Any]] = []

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/slow":  # the page's own code, arriving late (greenhouse_boards.html)
            time.sleep(0.8)
            self._send(b"ok")
            return
        path = FORMS / self.path.lstrip("/")
        if not path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        self._send(path.read_bytes())

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        head = f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode()
        msg = BytesParser(policy=default).parsebytes(head + body)
        fields: dict[str, Any] = {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            raw = part.get_payload(decode=True)
            payload = raw if isinstance(raw, bytes) else b""
            fields[str(name)] = (
                {"file": filename, "bytes": len(payload)} if filename else (payload.decode())
            )
        _Handler.posts.append(fields)
        self._send(b"<html><body><h2>Thank you for applying!</h2></body></html>")

    def _send(self, data: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_to_fields_strips_required_marks() -> None:
    out = to_fields(
        [
            {"key": "a", "type": "text", "label": "Full name✱", "required": False},
            {"key": "b", "type": "text", "label": "Email *", "required": False},
            {"key": "c", "type": "text", "label": "Phone", "required": False},
        ]
    )
    assert [(f.label, f.required) for f in out] == [
        ("Full name", True), ("Email", True), ("Phone", False),
    ]  # fmt: skip


@needs_browser
async def test_reads_greenhouse_style_form(site: str) -> None:
    read = await read_form(f"{site}/greenhouse.html")
    got = {f.label: (f.type, f.required) for f in read.fields}
    assert got == {
        "First Name": ("text", True),
        "Last Name": ("text", True),
        "Email": ("text", True),
        "Phone": ("text", False),
        "Resume/CV": ("file", True),  # hidden file inputs count
        "Why do you want to work here?": ("textarea", True),
        "How did you hear about us?": ("select", False),
    }
    why = next(f for f in read.fields if f.type == "textarea")
    assert why.max_chars == 500
    hear = next(f for f in read.fields if f.type == "select")
    assert hear.options == ["LinkedIn", "Friend"]
    assert read.captcha is False


@needs_browser
async def test_reads_lever_and_ashby_style_forms(site: str) -> None:
    lever = await read_form(f"{site}/lever.html")
    assert [(f.label, f.required) for f in lever.fields] == [
        ("Resume/CV", True), ("Full name", True), ("Email", True), ("Current location", False),
    ]  # fmt: skip
    ashby = await read_form(f"{site}/ashby.html")
    by_key = {f.key: f for f in ashby.fields}
    assert by_key["q_auth"].label == "Are you legally authorized to work in India?"
    assert by_key["q_auth"].type == "radio" and by_key["q_auth"].options == ["Yes", "No"]
    assert by_key["q_auth"].required
    assert by_key["q_consent"].type == "checkbox" and not by_key["q_consent"].required
    assert ashby.captcha is True


@needs_browser
async def test_no_form_is_an_error(site: str, tmp_path: Path) -> None:
    with pytest.raises(FormError):
        await read_form(f"{site}/missing.html")


@needs_browser
async def test_submit_fills_uploads_and_confirms(site: str, tmp_path: Path) -> None:
    read = await read_form(f"{site}/ashby.html")
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/ashby.html",
        read.fields,
        {
            "_systemfield_name": "Asha Rao",
            "_systemfield_email": "asha@example.com",
            "q_notice": "30 days",
            "q_auth": "Yes",
            "q_consent": "Yes",
        },  # fmt: skip
        {},
        tmp_path / "shot.png",
        tmp_path / "trace.zip",
    )
    assert result.confirmed and result.clicked and result.screenshot
    assert (tmp_path / "shot.png").exists() and (tmp_path / "trace.zip").exists()
    sent = _Handler.posts[-1]
    assert sent["_systemfield_name"] == "Asha Rao" and sent["q_auth"] == "yes"
    assert sent["q_consent"] == "on"

    lever = await read_form(f"{site}/lever.html")
    up = {"resume": Upload(resume, "Asha-Rao-Resume.pdf", "application/pdf")}
    result = await submit_form(
        f"{site}/lever.html",
        lever.fields,
        {"name": "Asha Rao", "email": "asha@example.com"},
        up,
        tmp_path / "shot2.png",
        tmp_path / "trace2.zip",
    )
    assert result.confirmed
    assert _Handler.posts[-1]["resume"] == {"file": "Asha-Rao-Resume.pdf", "bytes": 13}


@needs_browser
async def test_submit_without_a_form_is_not_clicked(site: str, tmp_path: Path) -> None:
    fields = [FormField(key="nope", label="Nope", type="text", required=True)]
    result = await submit_form(
        f"{site}/missing.html", fields, {"nope": "x"}, {}, tmp_path / "s.png", tmp_path / "t.zip"
    )
    assert not result.confirmed and not result.clicked and result.error


@needs_browser
async def test_reads_a_combobox_as_a_select(site: str) -> None:
    read = await read_form(f"{site}/greenhouse_boards.html")
    by_key = {f.key: f for f in read.fields}
    office = by_key["id:q_office"]
    assert (office.type, office.widget, office.required) == ("select", "combobox", True)
    assert office.options == ["Yes", "No"]
    assert office.label.startswith("Are you open to working in-person")
    assert by_key["first_name"].required and by_key["why"].type == "textarea"


GH_ANSWERS = {
    "first_name": "Asha",
    "email": "asha@example.com",
    "id:q_office": "Yes",
    "id:country": "India",
    "why": "To make AI systems safer.",
}


@needs_browser
async def test_submit_waits_for_the_page_and_fills_again_what_it_cleared(
    site: str, tmp_path: Path
) -> None:  # fmt: skip
    """The Approve bug on Greenhouse job-boards pages: every field came out empty."""
    read = await read_form(f"{site}/greenhouse_boards.html")
    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/greenhouse_boards.html", read.fields, GH_ANSWERS, {},
        tmp_path / "s.png", tmp_path / "t.zip",
    )  # fmt: skip
    assert result.confirmed, result.error
    assert _Handler.posts[-1] == {
        "first_name": "Asha",
        "email": "asha@example.com",
        "q_office": "Yes",
        "country": "IN",  # the picker shows "+91", not "India", once picked
        "why": "To make AI systems safer.",
    }


@needs_browser
async def test_a_form_that_refuses_submit_is_rejected_not_sent(site: str, tmp_path: Path) -> None:
    read = await read_form(f"{site}/greenhouse_boards.html")
    _Handler.posts.clear()
    answers = {k: v for k, v in GH_ANSWERS.items() if k != "why"}
    result = await submit_form(
        f"{site}/greenhouse_boards.html", read.fields, answers, {},
        tmp_path / "s.png", tmp_path / "t.zip",
    )  # fmt: skip
    assert result.clicked and result.rejected and not result.confirmed
    assert result.error is not None and "Why Anthropic?" in result.error
    assert "First Name" not in result.error and _Handler.posts == []


@needs_browser
async def test_an_option_the_combobox_lacks_stops_before_submit(site: str, tmp_path: Path) -> None:
    read = await read_form(f"{site}/greenhouse_boards.html")
    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/greenhouse_boards.html", read.fields, {**GH_ANSWERS, "id:q_office": "Maybe"},
        {}, tmp_path / "s.png", tmp_path / "t.zip",
    )  # fmt: skip
    assert not result.clicked and not result.confirmed and _Handler.posts == []
    assert result.error is not None and 'No option "Maybe"' in result.error


@needs_browser
async def test_a_spam_check_error_is_reported_as_not_sent(site: str, tmp_path: Path) -> None:
    """The Linear (Ashby) failure: no confirmation, and an error banner JAP didn't read."""
    read = await read_form(f"{site}/ashby_spam.html")
    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/ashby_spam.html", read.fields,
        {"_systemfield_name": "Asha Rao", "_systemfield_email": "asha@example.com"}, {},
        tmp_path / "s.png", tmp_path / "t.zip",
    )  # fmt: skip
    assert result.clicked and result.rejected and not result.confirmed
    assert result.error is not None and "flagged as possible spam" in result.error
    assert _Handler.posts == []


@needs_browser
async def test_yes_no_buttons_are_a_question_and_get_clicked(site: str, tmp_path: Path) -> None:
    """The Linear (Ashby) failure: a required Yes/No question JAP didn't see."""
    read = await read_form(f"{site}/ashby_buttons.html")
    tz = next(f for f in read.fields if f.widget == "buttons")
    assert (tz.label, tz.type, tz.required, tz.options) == (
        "Are you based in a US equivalent timezone?", "radio", True, ["Yes", "No"],
    )  # fmt: skip
    assert [f.label for f in read.fields] == ["Name", tz.label]  # not "Upload file"
    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/ashby_buttons.html", read.fields,
        {"_systemfield_name": "Asha Rao", tz.key: "No"}, {},
        tmp_path / "s.png", tmp_path / "t.zip",
    )  # fmt: skip
    assert result.confirmed, result.error
    assert _Handler.posts[-1] == {"_systemfield_name": "Asha Rao", "q_tz": "No"}

    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/ashby_buttons.html", read.fields, {"_systemfield_name": "Asha Rao"}, {},
        tmp_path / "s2.png", tmp_path / "t2.zip",
    )  # fmt: skip
    assert result.clicked and result.rejected and _Handler.posts == []
    assert result.error is not None and "This field is required" in result.error


@needs_browser
async def test_an_emailed_security_code_is_not_sent(site: str, tmp_path: Path) -> None:
    """The Anthropic retry: every field filled, then Greenhouse asked for an emailed code."""
    read = await read_form(f"{site}/greenhouse_code.html")
    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/greenhouse_code.html", read.fields, {"first_name": "Asha"}, {},
        tmp_path / "s.png", tmp_path / "t.zip",
    )  # fmt: skip
    assert result.clicked and result.rejected and result.needs_code and not result.confirmed
    assert result.error is not None and "security code" in result.error
    assert _Handler.posts == []


def test_confirmation_wording() -> None:
    for text in [
        "Thank you for applying to Linear!",
        "Your application was successfully submitted. We'll be in touch.",
        "Application submitted",
        "We've received your application",
    ]:
        assert CONFIRMATION.search(text), text
    assert not CONFIRMATION.search("Submit application")


def test_contact_rules() -> None:
    class P:
        full_name = "Asha Rao Kumar"
        email = "asha@example.com"
        phone = "+91 98765 43210"
        locations = ["Bangalore"]
        links = [{"label": "LinkedIn", "url": "https://linkedin.com/in/asha"},
                 {"label": "Site", "url": "https://asha.dev"}]  # fmt: skip

    def value(label: str, type_: str = "text") -> tuple[str, str] | None:
        return rules.contact_value(FormField(key="k", label=label, type=type_, required=True), P())  # type: ignore[arg-type]

    assert value("First Name") == ("first_name", "Asha")
    assert value("Last name") == ("last_name", "Rao Kumar")
    assert value("Full name") == ("name", "Asha Rao Kumar")
    assert value("E-mail address") == ("email", "asha@example.com")
    assert value("Mobile phone") == ("phone", "+91 98765 43210")
    assert value("LinkedIn Profile") == ("linkedin", "https://linkedin.com/in/asha")
    assert value("GitHub") is None  # no GitHub link on the profile: not invented
    assert value("Portfolio / website") == ("website", "https://asha.dev")
    assert value("Current location") == ("location", "Bangalore")
    assert value("Contact", "email") == ("email", "asha@example.com")
    assert value("Why us?") is None
    assert value("Name", "textarea") is None


def test_facts_only_i_know() -> None:
    def mine(label: str) -> bool:
        return rules.needs_my_fact(FormField(key="k", label=label, type="text", required=True))

    for label in [
        "What is your notice period?",
        "Expected salary (CTC)",
        "Earliest start date",
        "Will you now or in the future require visa sponsorship?",
        "Open to relocation?",
    ]:
        assert mine(label), label
    for label in ["Why do you want to join?", "Which languages do you use?", "Describe a project"]:
        assert not mine(label), label
