import pytest

from llm.redact import (
    PersonalDataBlocked,
    Sensitive,
    find_links,
    from_resume_text,
    guard,
    redact,
    restore,
)

ME = Sensitive(
    name="Asha Rao",
    emails=["asha.rao@example.com"],
    phones=["+91 98765 43210"],
    addresses=["12 Lake Road\nIndiranagar, Bengaluru 560038"],
    links=["https://www.linkedin.com/in/asharao", "https://github.com/asharao"],
)


@pytest.mark.parametrize(
    "spelling",
    [
        "Asha Rao",
        "ASHA RAO",
        "asha   rao",
        "Asha",
        "rao",
        "asha.rao@example.com",
        "ASHA.RAO@EXAMPLE.COM",
        "+91 98765 43210",
        "9876543210",
        "98765-43210",
        "+919876543210",
        "(98765) 43210",
        "12 Lake Road Indiranagar, Bengaluru 560038",
        "12 lake road",
        "Indiranagar, Bengaluru 560038",
        "https://www.linkedin.com/in/asharao",
        "linkedin.com/in/asharao",
        "www.linkedin.com/in/asharao/",
        "github.com/asharao",
        "https://github.com/asharao",
        "asharao",
    ],
)
def test_every_spelling_is_redacted_and_guarded(spelling: str) -> None:
    text = f"Contact: {spelling}. Skills: python."
    out = redact(text, ME).text
    guard(out, ME)  # nothing left to find
    with pytest.raises(PersonalDataBlocked):
        guard(text, ME)
    assert "python" in out


def test_name_inside_other_words_is_left_alone() -> None:
    out = redact("Raoul built a narrow tool", ME).text
    assert out == "Raoul built a narrow tool"


def test_generic_patterns_on_resume_text() -> None:
    text = "Mail me at someone@else.org or call 080 2345 6789, site https://me.dev/x"
    out = redact(text, Sensitive(), generic=True).text
    assert "someone@else.org" not in out and "2345" not in out and "me.dev" not in out


def test_years_are_not_mistaken_for_phones() -> None:
    text = "Engineer, 2017 - 2021. Senior, 2021 - now."
    assert redact(text, Sensitive(), generic=True).text == text


def test_restore_round_trip() -> None:
    r = redact("Regards, Asha Rao (github.com/asharao)", ME)
    assert "[NAME]" in r.text and "[LINK_2]" in r.text
    assert restore(r.text, ME, r.links) == "Regards, Asha Rao (https://github.com/asharao)"
    assert restore("Hi [PHONE] [LINK_9]", Sensitive(), {}) == "Hi  "


def test_values_found_in_resume() -> None:
    text = "Asha Rao\nasha.rao@example.com | +91 98765 43210 | github.com/asharao\nPython"
    found = from_resume_text(text)
    assert found.name == "Asha Rao"
    assert found.emails == ["asha.rao@example.com"]
    assert found.phones and "98765" in found.phones[0]
    assert find_links(text) == ["github.com/asharao"]
    guard(redact(text, found, generic=True).text, found)
