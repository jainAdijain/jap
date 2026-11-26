"""The form reader and filler against local copies of Greenhouse-, Lever- and Ashby-style
forms, in a real headless Chromium. Skipped when Chromium is not installed (CI installs it).
"""

import threading
import time
from collections.abc import Iterator
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from pipeline.apply import fields as rules
from pipeline.apply.form import (
    CONFIRMATION,
    PUZZLE_FOUND,
    FormError,
    FormField,
    Upload,
    confirmed_after,
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
        path = FORMS / self.path.split("?")[0].lstrip("/")
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
async def test_reads_a_form_drawn_after_the_network_goes_quiet(site: str) -> None:
    """Ashby's hosted page shows "Fetching application form" first (JAP-7)."""
    read = await read_form(f"{site}/ashby_late.html")
    assert [(f.key, f.required) for f in read.fields] == [
        ("_systemfield_name", True), ("_systemfield_email", True), ("q_why", True),
    ]  # fmt: skip


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
    # The form has an hCaptcha: everything is filled, and JAP stops before Submit for me to
    # finish it myself (JAP-11). Filling, uploading and confirming: ashby_atlan.html below.
    assert result.captcha and not result.clicked and not result.confirmed and result.screenshot
    assert (tmp_path / "shot.png").exists() and (tmp_path / "trace.zip").exists()
    assert _Handler.posts == []

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
    # A required field left empty stops before Submit now (JAP-11), naming only that field.
    assert not result.clicked and not result.confirmed
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
    assert not result.clicked and _Handler.posts == []  # stopped before Submit (JAP-11)
    assert result.error is not None and "US equivalent timezone" in result.error


def test_thanks_already_on_the_page_is_no_confirmation() -> None:
    """JAP-8: Ashby's banner thanks you for your interest before anything is sent."""
    banner = "Thanks for your interest and we appreciate your understanding!"
    assert not confirmed_after(banner, banner + " Submitting...")
    assert confirmed_after(banner, "Application submitted. Thanks for applying!")
    assert confirmed_after("", "Thank you for applying!")


@needs_browser
async def test_ashby_yes_no_without_a_type_and_a_banner(site: str, tmp_path: Path) -> None:
    """JAP-8 and JAP-9, as on Supabase's Ashby form: the Yes/No question is read and
    clicked, and a press that changes nothing is not taken for a confirmation."""
    read = await read_form(f"{site}/ashby_yesno.html")
    q = next(f for f in read.fields if f.widget == "buttons")
    assert (q.label, q.type, q.required, q.options) == (
        "Are you over the age of 18?", "radio", True, ["Yes", "No"],
    )  # fmt: skip
    assert [f.label for f in read.fields] == ["Name", q.label]

    unanswered = await submit_form(
        f"{site}/ashby_yesno.html", read.fields, {"_systemfield_name": "Asha Rao"}, {},
        tmp_path / "s1.png", tmp_path / "t1.zip",
    )  # fmt: skip
    assert not unanswered.clicked and not unanswered.confirmed  # stopped before Submit

    answered = await submit_form(
        f"{site}/ashby_yesno.html", read.fields,
        {"_systemfield_name": "Asha Rao", q.key: "Yes"}, {},
        tmp_path / "s2.png", tmp_path / "t2.zip",
    )  # fmt: skip
    assert answered.confirmed, answered.error


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


@needs_browser
async def test_an_emailed_security_code_is_typed_in_and_sent(site: str, tmp_path: Path) -> None:
    """A5: the code read from JAP's mailbox goes into the boxes, then Submit once more."""
    read = await read_form(f"{site}/greenhouse_code.html")
    asked: list[tuple[datetime, int]] = []

    async def get_code(since: datetime, length: int) -> str | None:
        asked.append((since, length))
        return "Ab3dE5fG"

    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/greenhouse_code.html", read.fields, {"first_name": "Asha"}, {},
        tmp_path / "s.png", tmp_path / "t.zip", get_code,
    )  # fmt: skip
    assert result.confirmed, result.error
    assert [p["security_code"] for p in _Handler.posts] == ["Ab3dE5fG"]
    assert len(asked) == 1 and asked[0][1] == 8


@needs_browser
async def test_no_code_in_time_or_a_wrong_code_sends_nothing(site: str, tmp_path: Path) -> None:
    read = await read_form(f"{site}/greenhouse_code.html")
    for code, said in [(None, "No security code reached"), ("Zz9zZz9z", "did not take")]:

        async def get_code(since: datetime, length: int, code: str | None = code) -> str | None:
            return code

        _Handler.posts.clear()
        result = await submit_form(
            f"{site}/greenhouse_code.html", read.fields, {"first_name": "Asha"}, {},
            tmp_path / "s.png", tmp_path / "t.zip", get_code,
        )  # fmt: skip
        assert result.rejected and result.needs_code and not result.confirmed
        assert result.error is not None and said in result.error
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


ATLAN_ANSWERS = {
    "_systemfield_name": "Asha Rao",
    "_systemfield_email": "asha@example.com",
    "_systemfield_phone": "+91 98765 43210",
    "q_sms": "Yes",
    "q_whatsapp": "No",
    "id:q_location": "Bengaluru, India",
    "_systemfield_resume": "resume.pdf",
    "q_linkedin": "https://linkedin.com/in/asha",
    "buttons:Are you currently eligible to work in the country you are applying for?": "Yes",
    "buttons:Will you require visa sponsorship?": "No",
    "q_source": "LinkedIn",
    "q_ack": "Yes",
}


@needs_browser
async def test_reads_every_ashby_field_type(site: str) -> None:
    """JAP-11, as on Atlan's form: radios, a location typeahead, Yes/No buttons, a radio list
    and a checkbox are all read, and all required from their titles' class."""
    read = await read_form(f"{site}/ashby_atlan.html")
    got = {f.key: (f.type, f.widget, f.required, f.options) for f in read.fields}
    assert got == {
        "_systemfield_name": ("text", "", True, []),
        "_systemfield_email": ("email", "", True, []),
        "_systemfield_phone": ("tel", "", True, []),
        "q_sms": ("radio", "", True, ["Yes", "No"]),
        "q_whatsapp": ("radio", "", True, ["Yes", "No"]),
        "id:q_location": ("select", "combobox", True, []),  # a typeahead lists nothing yet
        "_systemfield_resume": ("file", "", True, []),
        "q_linkedin": ("text", "", False, []),
        "buttons:Are you currently eligible to work in the country you are applying for?": (
            "radio", "buttons", True, ["Yes", "No"]),
        "buttons:Will you require visa sponsorship?": ("radio", "buttons", True, ["Yes", "No"]),
        "q_source": ("radio", "", True, ["LinkedIn", "Company website", "Referral"]),
        "q_ack": ("checkbox", "", True, ["I confirm the information I provided is accurate"]),
    }  # fmt: skip
    labels = {f.key: f.label for f in read.fields}
    assert labels["q_sms"] == "Do you agree to receive SMS updates about your application?"
    assert labels["q_ack"] == "Acknowledgement"
    assert labels["id:q_location"] == "Location"
    assert read.captcha is False


@needs_browser
async def test_ashby_form_is_filled_and_each_button_goes_to_its_question(
    site: str, tmp_path: Path
) -> None:
    """The eligibility question got the visa question's "No" (JAP-11): each answer is now
    clicked under its own question, the typeahead picks the place, and all is sent."""
    read = await read_form(f"{site}/ashby_atlan.html")
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 cv")
    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/ashby_atlan.html", read.fields, ATLAN_ANSWERS,
        {"_systemfield_resume": Upload(resume, "Asha-Rao.pdf", "application/pdf")},
        tmp_path / "s.png", tmp_path / "t.zip",
    )  # fmt: skip
    assert result.confirmed, result.error
    sent = _Handler.posts[-1]
    assert sent["_systemfield_resume"] == {"file": "Asha-Rao.pdf", "bytes": 11}
    del sent["_systemfield_resume"]
    assert sent == {
        "_systemfield_name": "Asha Rao",
        "_systemfield_email": "asha@example.com",
        "_systemfield_phone": "+91 98765 43210",
        "q_sms": "Yes",
        "q_whatsapp": "No",
        "q_location": "Bengaluru, Karnataka, India",
        "q_linkedin": "https://linkedin.com/in/asha",
        "q_eligible": "Yes",
        "q_visa": "No",
        "q_source": "LinkedIn",
        "q_ack": "on",
    }


@needs_browser
async def test_ashby_radios_named_anew_on_each_load_are_filled(site: str, tmp_path: Path) -> None:
    """JAP-16, as on ClickHouse's form: Ashby puts a new random id in front of each radio
    group's name on every load, so the name read is not the one the submit finds. The
    answer is picked by the question's own name and the form is sent."""
    read = await read_form(f"{site}/ashby_load_id.html")
    years = next(f for f in read.fields if f.key.endswith("_question_17759507004"))
    assert (years.type, years.required) == ("radio", True)
    assert years.options == ["0", "1", "2", "3", "4", "5+"]
    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/ashby_load_id.html", read.fields,
        {"_systemfield_name": "Asha Rao", years.key: "0"}, {},
        tmp_path / "s.png", tmp_path / "t.zip",
    )  # fmt: skip
    assert result.confirmed, result.error
    sent = {k.split("_", 1)[1] if "-" in k else k: v for k, v in _Handler.posts[-1].items()}
    assert sent == {"_systemfield_name": "Asha Rao", "question_17759507004": "0"}


@needs_browser
async def test_an_empty_required_field_stops_before_submit(site: str, tmp_path: Path) -> None:
    """A required field left empty is named, and Submit is never pressed (JAP-11)."""
    read = await read_form(f"{site}/ashby_atlan.html")
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 cv")
    answers = {k: v for k, v in ATLAN_ANSWERS.items() if k not in ("q_ack", "q_whatsapp")}
    _Handler.posts.clear()
    result = await submit_form(
        f"{site}/ashby_atlan.html", read.fields, answers,
        {"_systemfield_resume": Upload(resume, "cv.pdf", "application/pdf")},
        tmp_path / "s.png", tmp_path / "t.zip",
    )  # fmt: skip
    assert not result.clicked and not result.confirmed and not result.captcha
    assert result.error is not None and result.error.startswith("Stopped before Submit")
    assert "WhatsApp" in result.error and "Acknowledgement" in result.error
    assert _Handler.posts == []


@needs_browser
async def test_a_puzzle_stops_before_submit_and_is_never_touched(site: str, tmp_path: Path) -> None:
    """A picture puzzle on the form: read as a CAPTCHA, and submit stops before Submit with
    every field filled, so I can finish it myself (JAP-11). One that appears only after
    Submit holds the form: nothing is sent, and it is the same Finish yourself case."""
    read = await read_form(f"{site}/ashby_atlan.html?captcha")
    assert read.captcha is True
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 cv")
    files = {"_systemfield_resume": Upload(resume, "cv.pdf", "application/pdf")}
    _Handler.posts.clear()
    before = await submit_form(
        f"{site}/ashby_atlan.html?captcha", read.fields, ATLAN_ANSWERS, files,
        tmp_path / "s.png", tmp_path / "t.zip",
    )  # fmt: skip
    assert before.captcha and not before.clicked and before.error == PUZZLE_FOUND
    after = await submit_form(
        f"{site}/ashby_atlan.html?challenge", read.fields, ATLAN_ANSWERS, files,
        tmp_path / "s2.png", tmp_path / "t2.zip",
    )  # fmt: skip
    assert after.captcha and after.clicked and after.rejected and not after.confirmed
    assert _Handler.posts == []


def test_phone_gets_its_country_code() -> None:
    class P:
        full_name = "Asha Rao"
        email = "asha@example.com"
        phone = "98765 43210"
        address = None
        locations = ["Bengaluru, India"]
        links: list[dict[str, str]] = []

    phone = FormField(key="p", label="Phone", type="tel", required=True)
    assert rules.contact_value(phone, P(), country_picker=False) == ("phone", "+91 98765 43210")  # type: ignore[arg-type]
    assert rules.contact_value(phone, P()) == ("phone", "98765 43210")  # type: ignore[arg-type]
    P.phone = "+44 7700 900123"
    assert rules.contact_value(phone, P(), country_picker=False) == ("phone", "+44 7700 900123")  # type: ignore[arg-type]
    P.phone, P.locations = "98765 43210", ["Somewhere"]
    assert rules.contact_value(phone, P(), country_picker=False) == ("phone", "98765 43210")  # type: ignore[arg-type]
    picker = FormField(key="c", label="Country", type="select", required=True, widget="combobox")
    assert rules.has_country_picker([picker, phone])
    assert not rules.has_country_picker([phone])
    place = FormField(key="l", label="Location", type="select", required=True, widget="combobox")
    P.locations = ["Bengaluru, India"]
    assert rules.contact_value(place, P()) == ("location", "Bengaluru, India")  # type: ignore[arg-type]
