"""The trace of AI work that leaves JAP (PRD 1.33, O16, O17 level 1, O19, O22, O23): off
unless set, numbers and names only, never text, never in the way of the work, and sent by
one module. From 1.34 the runner's trace of a run goes out too, as one trace of many spans,
with the writer's and checker's reasons as codes (O24)."""

import io
import json
import struct
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import select

import llm.client as client
from config import get_settings
from database.models import LlmCall
from database.session import get_sessionmaker
from llm import PersonalDataBlocked, SpendCapReached, call
from llm.redact import Redaction, Sensitive
from observability import context, trace, writer
from pipeline import agent_doors, agent_run_trace, apply_trace, claude_reader, claude_writer
from pipeline.apply import pacing
from schemas.resume import ParsedResume
from tests.test_llm_call import GOOD, RESUME
from tests.test_redact import ME

BACKEND = Path(__file__).resolve().parent.parent
URL = "https://trace.example/otel/v1/traces"
# Nothing of the resume, of me, or of a reply may be in what is sent.
TEXT = [b"asha", b"rao", b"example.com", b"98765", b"FastAPI", b"Backend", b"python", b"Acme"]
# Two run traces as agent/trace.py writes them, a reader run and a writer run with its
# checker: a copy made by agent/tests/run_test.sh, which fails when the runner writes
# another shape than this.
RUNS = BACKEND / "tests" / "traces" / "agent_runs.json"
M = trace.META


def _varint(data: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        n |= (data[i] & 0x7F) << shift
        shift += 7
        i += 1
        if not data[i - 1] & 0x80:
            return n, i


def _fields(data: bytes) -> list[tuple[int, Any]]:
    """A protobuf message as (field number, value): an int, 8 bytes, or the bytes inside."""
    out: list[tuple[int, Any]] = []
    i = 0
    while i < len(data):
        key, i = _varint(data, i)
        if key & 7 == 0:
            value, i = _varint(data, i)
            out.append((key >> 3, value))
        elif key & 7 == 1:
            out.append((key >> 3, data[i : i + 8]))
            i += 8
        else:
            n, i = _varint(data, i)
            out.append((key >> 3, data[i : i + n]))
            i += n
    return out


def _attrs(span: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for number, pair in _fields(span):
        if number != 9:
            continue
        kv = dict(_fields(pair))
        ((kind, value),) = _fields(kv[2])
        if kind == 1:
            value = value.decode()
        elif kind == 4:
            value = struct.unpack("<d", value)[0]
        out[kv[1].decode()] = value
    return out


def spans(body: bytes) -> list[tuple[str, dict[str, Any], bool]]:
    """Each span of an OTLP request: its name, its attributes, and whether it ended well."""
    out = []
    for _, resource_spans in _fields(body):
        for number, scope_spans in _fields(resource_spans):
            for inner, span in _fields(scope_spans) if number == 2 else []:
                if inner == 2:
                    top = dict(_fields(span))
                    assert len(top[1]) == 16 and len(top[2]) == 8  # trace id, span id
                    out.append((top[5].decode(), _attrs(span), dict(_fields(top[15]))[3] == 1))
    return out


def links(body: bytes) -> list[tuple[str, bytes, bytes, bytes | None]]:
    """Each span of an OTLP request: its name, its trace, itself and its parent."""
    out = []
    for _, resource_spans in _fields(body):
        for number, scope_spans in _fields(resource_spans):
            for inner, span in _fields(scope_spans) if number == 2 else []:
                if inner == 2:
                    top = dict(_fields(span))
                    out.append((top[5].decode(), top[1], top[2], top.get(4)))
    return out


class Service:
    """Stands in for the trace service and keeps every request."""

    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.status_code = status
        self.error = error
        self.requests: list[tuple[str, dict[str, str], bytes]] = []

    def __call__(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> Any:
        self.requests.append((url, headers, content))
        if self.error is not None:
            raise self.error
        return self

    def spans(self) -> list[tuple[str, dict[str, Any], bool]]:
        trace.flush()
        return [s for _, _, body in self.requests for s in spans(body)]

    def sent(self) -> bytes:
        trace.flush()
        return b"".join(body for _, _, body in self.requests)


def _set(monkeypatch: pytest.MonkeyPatch, endpoint: str, headers: str) -> None:
    monkeypatch.setenv("JAP_TRACE_ENDPOINT", endpoint)
    monkeypatch.setenv("JAP_TRACE_HEADERS", headers)
    get_settings.cache_clear()


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[Service]:
    stub = Service()
    monkeypatch.setattr(httpx, "post", stub)
    _set(monkeypatch, URL, "x-api-key=k-123, Langsmith-Project=jap")
    yield stub
    trace.flush()
    get_settings.cache_clear()


class Model:
    """Stands in for OpenRouter, answering as it does: who served it and the token detail."""

    def __init__(self, replies: list[str], cost: float = 0.001) -> None:
        self.replies = replies
        self.cost = cost

    async def __call__(self, api_key: str, body: dict[str, Any], wait_s: float = 0) -> Any:
        return client.ChatResult(
            self.replies.pop(0),
            10,
            5,
            self.cost,
            "Google AI Studio",
            "openai/gpt-5.6-luna-1120",
            4,
            2,
        )


def test_only_numbers_ids_and_short_names_pass_the_allow_list() -> None:
    app_id = uuid.uuid4()
    kept = trace.clean(
        {
            "model": "anthropic/claude-sonnet-4.5",
            "tokens_in": 24430,
            "cost_usd": 0.0123,
            "outcome": "valid",
            "application_id": app_id,
            "not_saved_missing_field": 2,
            # None of these may pass: text, a name not on the list, an email as a provider,
            # an outcome not on its list, a title as an id, a sentence as a count.
            "purpose": "I would love to work at Acme",
            "company": "Acme",
            "answer": "Yes",
            "provider": "asha.rao@example.com",
            "outcome_text": "valid",
            "posting_id": "Senior Engineer",
            "mode": "claude",
            "tokens_out": "five",
            "attempt": True,
            "saved": -1,
            "not_saved_because_i_said_so": 1,
        }
    )
    assert kept == {
        "gen_ai.request.model": "anthropic/claude-sonnet-4.5",
        "gen_ai.usage.input_tokens": 24430,
        "langsmith.metadata.cost_usd": 0.0123,
        "langsmith.metadata.outcome": "valid",
        "langsmith.metadata.application_id": str(app_id),
        "langsmith.metadata.not_saved_missing_field": 2,
    }


def test_off_unless_both_settings_are_filled(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = Service()
    monkeypatch.setattr(httpx, "post", stub)
    for endpoint, headers in [
        ("", ""),
        (URL, ""),
        ("", "x-api-key=k"),
        ("http://plain.example/v1", "x-api-key=k"),
    ]:
        _set(monkeypatch, endpoint, headers)
        assert trace.target() is None
        attrs = trace.record("llm.answer", "llm", 1.0, 2.0, mode="api", tokens_in=3)
        assert attrs["gen_ai.usage.input_tokens"] == 3  # the same attributes, sent nowhere
    trace.flush()
    assert stub.requests == []
    get_settings.cache_clear()


def test_a_span_goes_out_as_otlp_with_the_headers_from_the_setting(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace.record(
        "door.reader.export",
        "chain",
        100.0,
        101.5,
        mode="agent",
        job="reader",
        door="export",
        postings=10,
        title="Senior Engineer at Acme",
    )
    trace.record("llm.answer", "llm", 100.0, 100.2, ok=False, mode="api", outcome="error")
    trace.record("a name with spaces", "chain", 1.0, 2.0)  # not a span name: not sent
    got = service.spans()
    url, headers, _ = service.requests[0]
    assert url == URL and headers["x-api-key"] == "k-123" and headers["Langsmith-Project"] == "jap"
    for base in ("https://trace.example/otel", "https://trace.example/otel/", URL + "/"):
        monkeypatch.setenv("JAP_TRACE_ENDPOINT", base)  # the base address, as LangSmith gives it
        get_settings.cache_clear()
        to = trace.target()
        assert to is not None and to[0] == URL
    assert headers["Content-Type"] == "application/x-protobuf"
    assert [(name, ok) for name, _, ok in got] == [
        ("door.reader.export", True),
        ("llm.answer", False),
    ]
    door = got[0][1]
    assert door["langsmith.metadata.postings"] == 10 and door["langsmith.span.kind"] == "chain"
    assert door["langsmith.metadata.env"] == get_settings().jap_env
    assert got[1][1]["gen_ai.operation.name"] == "chat"
    assert b"Acme" not in service.sent() and b"k-123" not in service.sent()


@pytest.mark.parametrize("stub", [Service(status=500), Service(error=OSError("no route"))])
def test_a_service_that_fails_costs_nothing(
    stub: Service, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(httpx, "post", stub)
    monkeypatch.setattr(trace, "_warned", False)
    _set(monkeypatch, URL, "x-api-key=k-123")
    trace.record("llm.answer", "llm", 1.0, 2.0, mode="api")
    trace.flush()
    assert len(stub.requests) == 1
    said = capsys.readouterr().err
    assert "traces are not being sent" in said and "k-123" not in said
    get_settings.cache_clear()


async def test_a_model_call_is_traced_in_numbers_and_never_in_text(
    db: str, service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O19, and the test O16 asks for: text in a trace fails the build."""
    monkeypatch.setattr(client, "chat", Model([GOOD]))
    app_id, posting_id = uuid.uuid4(), uuid.uuid4()
    with context.scope(application_id=app_id, posting_id=posting_id):
        async with get_sessionmaker()() as session:
            await call(
                session,
                "resume_parse",
                {"resume_text": RESUME},
                ParsedResume,
                sensitive=ME,
                generic_redact=frozenset({"resume_text"}),
            )
    ((name, attrs, ok),) = service.spans()
    assert name == "llm.resume_parse" and ok
    assert attrs == {
        "langsmith.metadata.env": get_settings().jap_env,
        "langsmith.metadata.mode": "api",
        "langsmith.metadata.harness": "none",
        "langsmith.metadata.purpose": "resume_parse",
        "langsmith.metadata.prompt_version": "v1",
        "gen_ai.request.model": get_settings().jap_model_extract,
        "gen_ai.response.model": "openai/gpt-5.6-luna-1120",
        "gen_ai.system": "google-ai-studio",
        "gen_ai.request.max_tokens": 6000,
        "langsmith.metadata.attempt": 1,
        "langsmith.metadata.outcome": "valid",
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 5,
        "gen_ai.usage.cache_read.input_tokens": 4,
        "gen_ai.usage.reasoning.output_tokens": 2,
        "langsmith.metadata.cost_usd": 0.001,
        "langsmith.metadata.latency_ms": attrs["langsmith.metadata.latency_ms"],
        "langsmith.metadata.application_id": str(app_id),
        "langsmith.metadata.posting_id": str(posting_id),
        "langsmith.metadata.thread_id": str(app_id),
        "session.id": str(app_id),
        "langsmith.span.kind": "llm",
        "gen_ai.operation.name": "chat",
    }
    body = service.sent().lower()
    for text in TEXT:
        assert text.lower() not in body, f"{text!r} reached the trace"
    async with get_sessionmaker()() as session:
        row = await session.scalar(select(LlmCall))
    assert row is not None and row.posting_id == posting_id and row.provider == "Google AI Studio"
    assert (row.attempt, row.outcome, row.tokens_cached, row.tokens_reasoning) == (1, "valid", 4, 2)


async def test_how_a_call_ended_is_on_its_row_and_its_span(
    db: str, service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ask(text: str = "Python", sensitive: Sensitive | None = None) -> None:
        async with get_sessionmaker()() as session:
            await call(
                session,
                "resume_parse",
                {"resume_text": text},
                ParsedResume,
                sensitive=sensitive or Sensitive(),
            )

    monkeypatch.setattr(client, "chat", Model(["I'd love to work at Acme.", GOOD]))
    await ask()  # a reply that is not the JSON asked for, then one that is
    with monkeypatch.context() as m:  # a redaction miss: the guard blocks the call
        m.setattr(
            sys.modules["llm.call"],
            "redact",
            lambda text, values, generic=False: Redaction(text, {}),
        )
        with pytest.raises(PersonalDataBlocked):
            await ask(RESUME, ME)
    monkeypatch.setattr(client, "chat", Model([GOOD], cost=2.5))
    await ask()
    with pytest.raises(SpendCapReached):
        await ask()
    async with get_sessionmaker()() as session:
        rows = (await session.scalars(select(LlmCall).order_by(LlmCall.created_at))).all()
    ended = [(r.attempt, r.outcome) for r in rows]
    assert ended == [
        (1, "retried"),
        (2, "valid"),
        (None, "blocked"),
        (1, "valid"),
        (None, "spend_cap"),
    ]
    got = service.spans()
    assert [(a.get("langsmith.metadata.outcome"), ok) for _, a, ok in got] == [
        ("retried", False),
        ("valid", True),
        ("blocked", False),
        ("valid", True),
        ("spend_cap", False),
    ]
    body = service.sent().lower()
    for text in TEXT:
        assert text.lower() not in body, f"{text!r} reached the trace"


def test_only_the_trace_module_talks_to_the_trace_service() -> None:
    """O22: one module sends, so one allow-list stands between JAP and the service."""
    knows = sorted(
        str(p.relative_to(BACKEND))
        for p in BACKEND.rglob("*.py")
        if "tests" not in p.parts and "jap_trace_" in p.read_text()
    )
    assert knows == ["config.py", "observability/trace.py"]


def test_what_an_import_said_becomes_counts_and_codes() -> None:
    lines = [
        "1: saved",
        "2: not saved (missing: skills)",
        "3: not saved (unknown or not wanted: work_mode)",
        "4: not saved (seniority: Input should be 'junior', 'mid' or 'senior')",
        "5: not saved (description changed since the export (or no description_hash))",
        "6: not saved (description changed while saving)",
        "7: not saved (posting closed, unfollowed or gone)",
        "8: not saved (already read)",
        "x: not saved (not a posting id)",
        "9: not saved (not an object of fields)",
        "pair:1: merged",
        "pair:2: kept apart",
        "pair:3: not saved (pair already decided or gone)",
        "pair:4: not saved (needs a pair id and {same: true or false, reason})",
        "saved 3 of 14",
    ]
    assert agent_doors.reader_import(lines) == {
        "items": 14,
        "saved": 3,
        "merged": 1,
        "kept_apart": 1,
        "not_saved": 11,
        "not_saved_missing_field": 1,
        "not_saved_unknown_field": 1,
        "not_saved_bad_value": 1,
        "not_saved_changed": 2,
        "not_saved_gone": 1,
        "not_saved_already_read": 1,
        "not_saved_bad_id": 1,
        "not_saved_bad_shape": 1,
        "not_saved_pair_gone": 1,
        "not_saved_bad_pair": 1,
    }
    answers = {"a": {"q1": "Yes", "_review": "A claim not in the resume"}, "b": {"q1": "No"}}
    lines = [
        "a: ready_for_review",
        "b: not answered (Nothing is waiting for an answer)",
        "resume:r1: read (12 skills)",
        "resume:r2: not read (already read, or gone)",
    ]
    assert agent_doors.writer_import(answers, lines) == {
        "items": 4,
        "held": 1,
        "answered": 1,
        "resumes_read": 1,
        "saved": 2,
        "not_saved": 2,
        "not_saved_not_answered": 1,
        "not_saved_resume_not_read": 1,
    }
    # Every count and code is a field the sender lets through.
    for counts in (agent_doors.reader_import(lines), agent_doors.writer_import(answers, lines)):
        assert len(trace.clean(counts)) == len(counts)
    assert all(f"not_saved_{code}" in trace.FIELDS for _, code in agent_doors.NOT_SAVED)


async def test_each_export_and_import_is_counted_at_the_door(
    db: str,
    sql: psycopg.Connection[Any],
    service: Service,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """O17, level 1: any harness, also one driven by hand, with no runner in between."""
    await claude_writer._main("export")
    exported = json.loads(capsys.readouterr().out)  # the export itself is unchanged
    assert exported["jobs"] == [] and "contract_version" in exported
    replies = {"Senior Engineer at Acme": {"skills": ["python"]}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(replies)))
    await claude_reader._main(["import"])
    assert "not saved (not a posting id)" in capsys.readouterr().out
    writer.flush()
    rows = sql.execute(
        "SELECT event, level, fields, message FROM log_event WHERE event LIKE 'agent.%' ORDER BY id"
    ).fetchall()
    assert [(r[0], r[1], r[2]["job"]) for r in rows] == [
        ("agent.export", "info", "writer"),
        ("agent.import", "info", "reader"),
    ]
    assert rows[0][2]["jobs"] == 0 and rows[0][2]["bytes"] > 100
    assert rows[1][2]["items"] == 1 and rows[1][2]["not_saved_bad_id"] == 1
    assert "Acme" not in json.dumps([r[2] for r in rows]) + rows[0][3] + rows[1][3]
    got = service.spans()
    assert [name for name, _, _ in got] == ["door.writer.export", "door.reader.import"]
    assert got[0][1]["langsmith.metadata.mode"] == "agent"
    assert got[1][1]["langsmith.metadata.not_saved"] == 1
    body = service.sent().lower()
    for text in TEXT:
        assert text.lower() not in body, f"{text!r} reached the trace"


def _runs() -> tuple[dict[str, Any], dict[str, Any]]:
    reader, written = json.loads(RUNS.read_text())
    return reader, written


def _hand_over(run: Any, monkeypatch: pytest.MonkeyPatch) -> int:
    """make agent-trace-send: the run's trace file on the command's standard input."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(run)))
    return agent_run_trace.main()


def test_a_run_of_the_runner_goes_out_as_one_trace(
    service: Service, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """O22, from 1.34: the run, its session with export and import, and each model request
    and tool call, as parents and children of one trace."""
    reader, _ = _runs()
    assert _hand_over(reader, monkeypatch) == 0
    assert capsys.readouterr().out == "sent 9 spans\n"
    ((url, headers, body),) = service.requests
    assert url == URL and headers["x-api-key"] == "k-123"
    got = {name: attrs for name, attrs, _ in spans(body)}
    assert sorted(name for name, _, _ in spans(body)) == [
        "model.request",
        "model.request",
        "model.request",
        "run.reader",
        "session.reader",
        "step.export",
        "step.import",
        "tool.Read",
        "tool.Write",
    ]
    tied = links(body)
    assert len({trace_id for _, trace_id, _, _ in tied}) == 1  # one trace
    ids = {name: span_id for name, _, span_id, _ in tied}
    parents = {name: parent for name, _, _, parent in tied}
    assert parents["run.reader"] is None
    assert parents["session.reader"] == ids["run.reader"]
    for name in ("step.export", "step.import", "model.request", "tool.Read", "tool.Write"):
        assert parents[name] == ids["session.reader"]
    run, session = got["run.reader"], got["session.reader"]
    assert run["langsmith.span.kind"] == "chain" and run[M + "mode"] == "agent"
    assert run[M + "run_stamp"] == reader["stamp"]
    assert (run[M + "unread_before"], run[M + "unread_after"]) == (1, 0)
    assert run[M + "sessions"] == 1 and run[M + "saved"] == 1
    assert run[M + "total_tokens_in"] == 6300
    # O20: who did the work, on which prompt and contract.
    assert session[M + "harness"] == "fake" and session["gen_ai.request.model"] == "fake-1"
    assert session[M + "prompt_version"] == reader["sessions"][0]["prompt_version"]
    assert session[M + "contract_version"] == 1
    assert session[M + "level"] == 3 and session[M + "shards"] == 1
    assert session[M + "loaded_tools"] == 2
    # Tokens are usage on a model request only, so a service that adds a trace up counts
    # them once; the session and the run carry totals under names of their own.
    asked = [a for name, a, _ in spans(body) if name == "model.request"]
    assert sorted(a["gen_ai.usage.input_tokens"] for a in asked) == [1100, 2100, 3100]
    assert all(a["langsmith.span.kind"] == "llm" for a in asked)
    assert not any(k.startswith("gen_ai.usage") for k in {**run, **session})
    used = got["tool.Read"]
    assert used["langsmith.span.kind"] == "tool" and used[M + "file"] == "in.json"
    # Sent again (a run's trace that waited): the same trace and the same spans, not new ones.
    assert _hand_over(reader, monkeypatch) == 0
    assert sorted(links(service.requests[1][2])) == sorted(tied)


def test_reasons_go_out_as_codes_and_every_field_of_a_run_trace_is_allowed(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O24: why the writer left a job out and what the checker fixed, as counts of codes.
    And the door builds no field the allow-list would drop, for a file the runner wrote."""
    reader, written = _runs()
    assert _hand_over(written, monkeypatch) == 0
    got = spans(service.sent())
    by_name = {name: attrs for name, attrs, _ in got}
    tops = [name for name, _, _ in got if name.startswith(("run.", "session."))]
    assert tops == ["run.writer", "session.writer", "session.checker"]
    wrote, checked = by_name["session.writer"], by_name["session.checker"]
    run = by_name["run.writer"]
    assert wrote[M + "left_out"] == 1 and wrote[M + "jobs"] == 2
    assert wrote[M + "why_fact_not_in_bank"] == 1
    assert wrote[M + "fact_salary"] == 1 and wrote[M + "fact_notice_period"] == 1
    assert wrote[M + "reasons_dropped"] == 4 and wrote[M + "answered"] == 1
    assert checked[M + "why_unsupported_claim"] == 1 and checked[M + "checked_ok"] == 1
    assert run[M + "left_out"] == 1 and run[M + "jobs_before"] == 2
    assert run[M + "why_unsupported_claim"] == 1
    # No id of an application goes out, only counts.
    assert b"11111111-1111-4111-8111-111111111111" not in service.sent()
    for one in (reader, written):
        built = agent_run_trace.tree(one)
        assert built is not None
        for node in built[1]:
            given = {k: v for k, v in node.fields.items() if v is not None}
            dropped = [k for k, v in given.items() if not trace.clean({k: v})]
            assert not dropped, f"{node.name}: {dropped} would not be sent"


def test_text_in_a_run_trace_file_is_never_sent(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test O16 asks for, at the runner's door: the file is written on the Mac, and
    whatever is in it, only what the allow-list knows goes out."""
    words = "Asha Rao asha.rao@example.com wrote Backend services in Python and FastAPI at Acme"

    def stuffed(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            inner = {k: stuffed(v, k) for k, v in value.items()}
            return {**inner, "company": words, "answer": words, words: 1}
        if isinstance(value, list):
            return [stuffed(v, key) for v in value] + ([words] if key != "sessions" else [])
        keep = key in ("job", "role", "kind")  # so that there still are spans to look at
        return value if keep or not isinstance(value, str) else words

    _, written = _runs()
    assert _hand_over(stuffed(written), monkeypatch) == 0
    names = sorted({name for name, _, _ in service.spans()})
    assert names == ["model.request", "run.writer", "session.checker", "session.writer",
                     "step.export", "step.import", "tool.other"]  # fmt: skip
    body = service.sent().lower()
    for text in TEXT:
        assert text.lower() not in body, f"{text!r} reached the trace"


def test_a_run_trace_that_cannot_be_sent_is_said_so_the_runner_keeps_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """O22: "not sent" and exit code 1 keep the file marked for the next run; "off" and
    "not a run trace" do not, since trying again would change nothing."""
    reader, _ = _runs()
    stub = Service(status=401)
    monkeypatch.setattr(httpx, "post", stub)
    _set(monkeypatch, URL, "x-api-key=k-123")
    assert _hand_over(reader, monkeypatch) == 1
    said = capsys.readouterr()
    assert said.out == "not sent (RuntimeError: the trace service answered 401)\n"
    assert "k-123" not in said.out + said.err and len(stub.requests) == 1
    monkeypatch.setattr(httpx, "post", Service(error=OSError("no route to k-123.example")))
    assert _hand_over(reader, monkeypatch) == 1
    assert capsys.readouterr().out.startswith("not sent (OSError")
    for not_a_run in ({}, [1, 2], {**reader, "job": "Senior Engineer at Acme"}, "words"):
        assert _hand_over(not_a_run, monkeypatch) == 0
        assert capsys.readouterr().out == "not a run trace\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO("not JSON"))
    assert agent_run_trace.main() == 0 and capsys.readouterr().out == "not a run trace\n"
    _set(monkeypatch, "", "")
    assert _hand_over(reader, monkeypatch) == 0
    assert capsys.readouterr().out == "off\n" and len(stub.requests) == 1
    get_settings.cache_clear()


# 1.38 (O26) ------------------------------------------------------------------------------


async def test_a_door_the_runner_called_sits_inside_its_runs_trace(
    db: str,
    sql: psycopg.Connection[Any],
    service: Service,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One run, one trace: the export's span is a child of the run's root span, not a trace
    of its own. By hand, with no stamp, it stays a trace of its own."""
    reader, _ = _runs()
    stamp = reader["stamp"]
    monkeypatch.setenv("JAP_RUN_STAMP", stamp)
    await claude_writer._main("export")
    monkeypatch.setenv("JAP_RUN_STAMP", "not a stamp")
    await claude_writer._main("export")
    capsys.readouterr()
    doors = links(service.sent())
    built = agent_run_trace.tree({**reader, "job": "writer"})
    assert built is not None
    key, _ = built
    run_trace, run_span = trace._digest(f"jap:{key}", 16), trace._digest(f"jap:{key}/run", 8)
    (name, trace_id, _, parent), (_, other_trace, _, no_parent) = doors
    assert name == "door.writer.export"
    assert (trace_id, parent) == (run_trace, run_span)
    assert other_trace != run_trace and no_parent is None
    attrs = [a for _, a, _ in service.spans()]
    assert attrs[0][M + "run_stamp"] == stamp and M + "run_stamp" not in attrs[1]


def test_a_session_says_what_an_item_cost_and_the_input_not_cached(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader, _ = _runs()
    assert _hand_over(reader, monkeypatch) == 0
    got = {name: attrs for name, attrs, _ in service.spans()}
    session, run = got["session.reader"], got["run.reader"]
    s = reader["sessions"][0]
    handed = s["items"]["postings"] + s["items"]["pairs"]
    assert session[M + "tokens_per_item"] == s["tokens"]["in"] // handed
    assert session[M + "uncached_tokens_in"] == s["tokens"]["in"] - s["tokens"]["cache_read"]
    totals = reader["totals"]
    assert run[M + "uncached_tokens_in"] == totals["tokens_in"] - totals["cache_read"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        ("The form page answered 403", "form_403"),
        ("No application form found on the page", "no_form"),
        ("Submitted, but the page did not confirm the application", "no_confirmation"),
        ("Submit outcome unknown", "outcome_unknown"),
        ('No option "No experience" for "How would you describe..."', "option_missing"),
        ("Locator.fill: Cannot type text into input[type=number]", "fill_error"),
        ("No confirmation email within 14 days", "expired"),
        ("argument of type 'int' is not iterable", "other"),
        (None, "other"),
    ],
)
def test_why_an_application_failed_is_a_code(error: str | None, code: str) -> None:
    assert apply_trace.failure_code(error) == code
    assert code in trace.FAILURES


async def test_an_application_sent_or_failed_is_a_span_of_counts_and_codes(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O26: why it failed as a code, never its error; and of the writer's answers, how many
    were changed before it went."""

    async def ats(session: Any, app: Any) -> str:
        return "ashby"

    monkeypatch.setattr(pacing, "ats_of", ats)
    app = SimpleNamespace(
        id=uuid.uuid4(), posting_id=uuid.uuid4(), run_id=None, mode="review", attempts=1,
        submit_started_at=None, hold_reason=None, created_at=None,
        error="Submitted, but Asha Rao's page at acme.example did not confirm",
        answers={"why": "I build payment APIs at Acme.", "notice": "Immediately", "x": "1"},
        writer_answers={"why": "I build payment APIs.", "notice": "Immediately"},
    )  # fmt: skip
    await apply_trace.failed(None, app)  # type: ignore[arg-type]
    await apply_trace.sent(None, app)  # type: ignore[arg-type]
    (failed, f, failed_ok), (sent, s, _) = service.spans()
    assert (failed, failed_ok, f[M + "failure"], f[M + "ats"]) == (
        "apply.failed",
        False,
        "no_confirmation",
        "ashby",
    )
    assert f[M + "apply_mode"] == "review" and f[M + "pressed"] == 0
    assert sent == "apply.sent"
    assert (s[M + "answers"], s[M + "writer_answers"], s[M + "edited"]) == (3, 2, 1)
    body = service.sent().lower()
    for text in ("asha", "acme", "payment", "immediately", "did not confirm"):
        assert text.encode() not in body
