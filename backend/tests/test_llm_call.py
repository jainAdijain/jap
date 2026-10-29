import json
import sys
from typing import Any

import pytest
from sqlalchemy import func, select

import llm.client as client
from database.models import LlmCall
from database.session import get_sessionmaker
from llm import PersonalDataBlocked, SpendCapReached, call
from llm.redact import Redaction, Sensitive
from schemas.resume import ParsedResume
from tests.test_redact import ME

RESUME = (
    "Asha Rao, asha.rao@example.com, +91 98765 43210, github.com/asharao\n"
    "Backend engineer: Python, FastAPI, Postgres."
)


class Stub:
    """Stands in for OpenRouter and fails the test if it ever sees a personal value."""

    def __init__(self, replies: list[str], cost: float = 0.001) -> None:
        self.replies = replies
        self.cost = cost
        self.bodies: list[dict[str, Any]] = []

    async def __call__(self, api_key: str, body: dict[str, Any], wait_s: float = 0) -> Any:
        dumped = json.dumps(body).lower()
        for value in ["asha", "rao", "example.com", "98765", "asharao", "lake road"]:
            assert value not in dumped, f"{value!r} reached the model"
        assert body["provider"] == {"data_collection": "deny"}
        self.bodies.append(body)
        return client.ChatResult(self.replies.pop(0), 10, 5, self.cost)


GOOD = json.dumps({"skills": ["python"], "titles": ["Backend Engineer"], "years_experience": 5})


async def _count() -> int:
    async with get_sessionmaker()() as session:
        return int(await session.scalar(select(func.count()).select_from(LlmCall)) or 0)


async def test_call_redacts_logs_and_validates(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = Stub([GOOD])
    monkeypatch.setattr(client, "chat", stub)
    async with get_sessionmaker()() as session:
        result = await call(
            session,
            "resume_parse",
            {"resume_text": RESUME},
            ParsedResume,
            sensitive=ME,
            generic_redact=frozenset({"resume_text"}),
        )
    assert result.output.skills == ["python"]
    assert "[NAME]" in result.model_input and "Python, FastAPI" in result.model_input
    async with get_sessionmaker()() as session:
        row = await session.scalar(select(LlmCall))
    assert row is not None and float(row.cost_usd or 0) == 0.001
    assert len(row.input_sha256) == 64 and row.purpose == "resume_parse"


async def test_one_retry_on_invalid_output(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "chat", Stub(["not json", GOOD]))
    async with get_sessionmaker()() as session:
        result = await call(
            session, "resume_parse", {"resume_text": "Python"}, ParsedResume, sensitive=Sensitive()
        )
    assert result.output.titles == ["Backend Engineer"]
    assert await _count() == 2


async def test_guard_blocks_and_nothing_is_sent(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = Stub([GOOD])
    monkeypatch.setattr(client, "chat", stub)
    # Simulate a redaction miss: the guard must still stop the call.
    monkeypatch.setattr(
        sys.modules["llm.call"], "redact", lambda text, values, generic=False: Redaction(text, {})
    )
    async with get_sessionmaker()() as session:
        with pytest.raises(PersonalDataBlocked):
            await call(session, "resume_parse", {"resume_text": RESUME}, ParsedResume, sensitive=ME)
    assert stub.bodies == []
    async with get_sessionmaker()() as session:
        row = await session.scalar(select(LlmCall))
    assert row is not None and row.blocked


async def test_spend_cap_stops_calls(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "chat", Stub([GOOD, GOOD], cost=2.5))
    async with get_sessionmaker()() as session:
        await call(
            session, "resume_parse", {"resume_text": "Python"}, ParsedResume, sensitive=Sensitive()
        )
        with pytest.raises(SpendCapReached):
            await call(
                session,
                "resume_parse",
                {"resume_text": "Python"},
                ParsedResume,
                sensitive=Sensitive(),
            )
