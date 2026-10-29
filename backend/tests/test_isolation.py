from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def test_only_the_client_talks_to_openrouter() -> None:
    """Every model call must pass through llm.call() and its redact module."""
    offenders = [
        str(p.relative_to(BACKEND))
        for p in BACKEND.rglob("*.py")
        if "tests" not in p.parts and "openrouter.ai" in p.read_text()
    ]
    assert offenders == ["llm/client.py"]
