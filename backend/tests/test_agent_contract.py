"""The swappable AI harness (PRD 1.30, AH1 and AH2): the reader and writer modes are named
agent and api (claude and openrouter still mean the same), and both exports carry the
contract version and every key the JSON Schemas in agent/contract/ promise a harness."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from config import Settings
from llm.redact import Sensitive
from pipeline import claude_reader, claude_writer
from pipeline.agent_contract import CONTRACT_VERSION

# The schemas sit next to the runner at the repo root; the app container mounts backend/
# only, so these checks run in CI and on a full checkout.
CONTRACT = Path(__file__).resolve().parents[2] / "agent" / "contract"
needs_contract = pytest.mark.skipif(not CONTRACT.is_dir(), reason="agent/contract not mounted")
ME = Sensitive(name="Asha Rao", emails=["asha.rao@example.com"])


@pytest.mark.parametrize(
    ("given", "mode"),
    [("agent", "agent"), ("api", "api"), ("claude", "agent"), ("openrouter", "api"),
     ("Claude", "agent")],
)  # fmt: skip
def test_old_mode_names_still_work(given: str, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAP_READER", given)
    monkeypatch.setenv("JAP_WRITER", given)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.jap_reader == mode and settings.jap_writer == mode


def test_agent_is_the_default_and_nonsense_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JAP_READER", raising=False)
    monkeypatch.delenv("JAP_WRITER", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.jap_reader == "agent" and settings.jap_writer == "agent"
    monkeypatch.setenv("JAP_READER", "gpt")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def _required(schema: dict[str, Any], data: Any, where: str = "") -> list[str]:
    """Keys the schema requires that the data lacks, walking objects and arrays."""
    missing: list[str] = []
    if isinstance(data, dict):
        missing += [f"{where}.{k}" for k in schema.get("required", []) if k not in data]
        for key, sub in schema.get("properties", {}).items():
            if key in data:
                missing += _required(sub, data[key], f"{where}.{key}")
    elif isinstance(data, list) and "items" in schema:
        for i, item in enumerate(data):
            missing += _required(schema["items"], item, f"{where}[{i}]")
    return missing


def _schema(name: str) -> dict[str, Any]:
    schema: dict[str, Any] = json.loads((CONTRACT / f"{name}.schema.json").read_text())
    assert schema["properties"]["contract_version"]["const"] == CONTRACT_VERSION
    return schema


@needs_contract
def test_reader_export_keeps_the_contract() -> None:
    posting = {
        "posting_id": "p1",
        "description_hash": "h1",
        "keys": ["skills"],
        "title": "Engineer",
        "locations": ["India"],
        "description_text": "Python.",
    }
    side = {"title": "Engineer", "locations": ["India"], "description_text": "Python."}
    pair = {"pair_id": "d1", "job_a": side, "job_b": side}  # fmt: skip
    out = claude_reader.build_export([posting], 1, False, ME, [pair])
    assert out["contract_version"] == CONTRACT_VERSION
    assert _required(_schema("reader-export"), out) == []


@needs_contract
def test_writer_export_keeps_the_contract() -> None:
    out = claude_writer.build_export(
        [{"application_id": "a1", "company": "Acme", "title": "Backend Engineer",
          "job_description": "Python.",
          "questions": [{"key": "why", "label": "Why Acme?", "reason": "waiting"}]}],
        "Python.", [("Notice period", "30 days")], ME, [("r1", "Python, 8 years.")],
    )  # fmt: skip
    assert out["contract_version"] == CONTRACT_VERSION
    assert _required(_schema("writer-export"), out) == []


@needs_contract
def test_every_job_prompt_names_the_files_the_runner_uses() -> None:
    """agent/run.sh hands a harness in.json and reads back the file named here; the reader
    gets the batch in its prompt and answers in its reply (PRD 1.41, AH11)."""
    jobs = CONTRACT.parent / "jobs"
    reader = (jobs / "reader.md").read_text()
    assert "`in.json`" in reader and "Reply with one JSON object and nothing else" in reader
    assert "`draft.json`" in (jobs / "writer.md").read_text()
    checker = (jobs / "checker.md").read_text()
    assert "`out.json`" in checker and "`findings.txt`" in checker
