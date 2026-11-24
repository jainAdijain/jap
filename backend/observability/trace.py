"""The one module that talks to the trace service (PRD 1.33, O16, O22, O23; 1.34, O24).

A trace of AI work holds numbers, ids and names from short sets, never text. This is where
that is enforced for everything JAP sends: record() takes fields by name, keeps only the
names on FIELDS, and keeps a value only when it is what that name allows (a count, a JAP id,
one of a fixed set, or a short identifier such as a model name). Anything else is dropped,
so no caller can send a prompt, job text, an answer, a company or a title, even by mistake.
A test fails the build if text gets through (tests/test_trace.py).

Spans go out as OpenTelemetry traces over HTTPS, in OTLP's protobuf form, which every
service takes and LangSmith's own examples use. The few messages needed are written out
here by hand, so there is no trace service's library in JAP, and moving from LangSmith to
Sentry, Langfuse or Logfire is JAP_TRACE_ENDPOINT and JAP_TRACE_HEADERS and nothing else.
Off unless both are set; JAP works the same without them. Where LangSmith reads a name it
is used: gen_ai.* for the model and tokens, langsmith.metadata.* for JAP's own fields, and
thread_id (session.id elsewhere) to group the calls of one application or one run (O19).

Sending never delays or fails the work it describes. record() only puts the span on a
queue; one background thread posts it; a post that fails is dropped with one line on stderr
(the llm_call row, or the door's log_event row, is still the record). A command that exits
straight away, as the export and import doors do, calls flush() first, which waits a few
seconds at most. The service's key is in JAP_TRACE_HEADERS and is never logged or handed on.

The runner's trace of a run is the one thing sent another way (send_tree, from 1.34). It is a
whole trace of many spans (the run, its sessions, each model request and tool call), handed
over by a command whose only work is sending it, so that caller waits and is told whether
the service took it: the runner keeps a trace that could not be sent and tries again at the
start of the next run (O22). The same allow-list stands in front of it. Its ids are worked
out from the run's own stamp, so a trace sent twice carries the same ids both times.
"""

import hashlib
import os
import queue
import re
import struct
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from config import get_settings

Kind = Literal["llm", "chain", "tool"]
Value = str | int | float | bool
Check = Callable[[Any], Value | None]

# A harness, a model, a provider, a prompt version: a short identifier with no spaces. No @
# either, so an email address can never pass as one.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,79}$")
META = "langsmith.metadata."
MODES = ("agent", "api")
# O19: how a model call ended.
OUTCOMES = ("valid", "retried", "blocked", "spend_cap", "error")
JOBS = ("reader", "writer")
DOORS = ("export", "import")
# Why an import did not save an item (pipeline/agent_doors.py turns JAP's own lines into these).
REASONS = (
    "unknown_field",
    "missing_field",
    "bad_value",
    "bad_id",
    "bad_shape",
    "gone",
    "changed",
    "already_read",
    "pair_gone",
    "bad_pair",
    "not_answered",
    "resume_not_read",
    "other",
)
# The runner's trace (agent/trace.py writes it, pipeline/agent_run_trace.py reads it). Who
# worked in a session, why one failed (O18; no_answer is a run in which JAP itself did not
# answer), and the files of a batch folder a tool may be said to have touched.
ROLES = ("reader", "writer", "checker")
CAUSES = (
    "not_logged_in",
    "slept",
    "timeout",
    "usage_limit",
    "no_output",
    "export_failed",
    "import_failed",
    "no_answer",
)
FILES = (
    "in.json",
    "out.json",
    "draft.json",
    "findings.txt",
    "reasons.json",
    "prompt.md",
    "failures.md",
    "other",
    "outside",
)
# O24: why a writer or checker left a job out, held it or fixed it, and which kind of fact
# only the person knows the answer bank did not give. The same lists as agent/trace.py and
# the job prompts; agent/tests/run_test.sh keeps them in step.
WHY = (
    "fact_not_in_bank",
    "not_in_resume",
    "no_option_fits",
    "unclear_question",
    "unsupported_claim",
    "missing_answer",
    "bad_option",
    "too_long",
    "cover_letter_rule",
    "placeholder",
    "ai_banned",
    "other",
)
FACTS = (
    "salary",
    "notice_period",
    "grades",
    "travel",
    "nationality",
    "work_permit",
    "gender",
    "ethnicity",
    "disability",
    "other",
)
# What a harness loaded at the start of a session, what the checker found, and JAP's status
# line before and after a run: each a count under a name built from these.
LOADED = ("tools", "mcp_servers", "plugins", "skills", "agents")
CHECKED = ("ok", "fixed", "held", "left_out")
WAITING = ("unread", "pairs", "jobs", "resumes")
STAMP = re.compile(r"^\d{8}-\d{6}-\d{1,8}$")
COUNTS = (
    "attempt",
    "latency_ms",
    "contract_version",
    "shard",
    "shards",
    "postings",
    "pairs",
    "jobs",
    "questions",
    "cover_letters",
    "resumes",
    "bank_entries",
    "bytes",
    "unread",
    "items",
    "saved",
    "merged",
    "kept_apart",
    "not_saved",
    "answered",
    "held",
    "resumes_read",
    # From the runner's trace: a run and its sessions.
    "level",
    "exit_code",
    "seconds",
    "sessions",
    "failed",
    "traced",
    "model_requests",
    "tool_calls",
    "turns",
    "batch",
    "batch_size",
    "in_bytes",
    "out_bytes",
    "tokens_in_estimate",
    "tokens_out_estimate",
    "total_tokens_in",
    "total_tokens_out",
    "total_cache_read",
    "total_cache_write",
    "model_ms",
    "tool_ms",
    "denied",
    "input_changed",
    "written",
    "resumes_written",
    "pairs_settled",
    "left_out",
    "not_answered",
    "reasons_dropped",
    "steps_not_sent",
)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "host.docker.internal")
MAX_QUEUE = 1_000
BATCH = 50
WAIT_S = 5.0
# A run's trace, sent whole: spans to a request, and how long one request may take.
TREE_BATCH = 500
TREE_WAIT_S = 20.0


def _count(value: Any) -> Value | None:
    ok = isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**53
    return value if ok else None


def _amount(value: Any) -> Value | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    return float(value)


def _name(value: Any) -> Value | None:
    return value if isinstance(value, str) and NAME.match(value) else None


def _id(value: Any) -> Value | None:
    if isinstance(value, uuid.UUID):
        return str(value)
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _one_of(allowed: tuple[str, ...]) -> Check:
    return lambda value: value if isinstance(value, str) and value in allowed else None


def _stamp(value: Any) -> Value | None:
    """A run's stamp as agent/run.sh makes it: date, time and process id, digits only."""
    return value if isinstance(value, str) and STAMP.match(value) else None


def _counts(names: tuple[str, ...], pattern: str) -> dict[str, tuple[str, Check]]:
    """A count for each name on a list, under a field name built from it."""
    return {pattern.format(name): (META + pattern.format(name), _count) for name in names}


# Every field a trace may carry: the name it is sent under, and what its value must be.
FIELDS: dict[str, tuple[str, Check]] = {
    "model": ("gen_ai.request.model", _name),
    "model_served": ("gen_ai.response.model", _name),
    "provider": ("gen_ai.system", _name),
    "max_tokens": ("gen_ai.request.max_tokens", _count),
    "tokens_in": ("gen_ai.usage.input_tokens", _count),
    "tokens_out": ("gen_ai.usage.output_tokens", _count),
    "tokens_cached": ("gen_ai.usage.cache_read.input_tokens", _count),
    "tokens_cache_write": ("gen_ai.usage.cache_creation.input_tokens", _count),
    "tokens_reasoning": ("gen_ai.usage.reasoning.output_tokens", _count),
    "mode": (META + "mode", _one_of(MODES)),
    "env": (META + "env", _name),
    "harness": (META + "harness", _name),
    "purpose": (META + "purpose", _name),
    "prompt_version": (META + "prompt_version", _name),
    "outcome": (META + "outcome", _one_of(OUTCOMES)),
    "job": (META + "job", _one_of(JOBS)),
    "door": (META + "door", _one_of(DOORS)),
    "cost_usd": (META + "cost_usd", _amount),
    "run_id": (META + "run_id", _id),
    "application_id": (META + "application_id", _id),
    "posting_id": (META + "posting_id", _id),
    "thread_id": (META + "thread_id", _id),
    "role": (META + "role", _one_of(ROLES)),
    "cause": (META + "cause", _one_of(CAUSES)),
    "file": (META + "file", _one_of(FILES)),
    "tool": (META + "tool", _name),
    "checker": (META + "checker", _name),
    "harness_version": (META + "harness_version", _name),
    "run_stamp": (META + "run_stamp", _stamp),
    **{name: (META + name, _count) for name in COUNTS},
    **_counts(REASONS, "not_saved_{}"),
    **_counts(CAUSES, "cause_{}"),
    **_counts(WHY, "why_{}"),
    **_counts(FACTS, "fact_{}"),
    **_counts(LOADED, "loaded_{}"),
    **_counts(CHECKED, "checked_{}"),
    **_counts(WAITING, "{}_before"),
    **_counts(WAITING, "{}_after"),
}


def clean(fields: dict[str, Any]) -> dict[str, Value]:
    """What of these fields may go into a trace, under the names it is sent with (O16)."""
    out: dict[str, Value] = {}
    for key, value in fields.items():
        spec = FIELDS.get(key)
        kept = spec[1](value) if spec is not None and value is not None else None
        if spec is not None and kept is not None:
            out[spec[0]] = kept
    return out


# OTLP's protobuf messages, by field number (opentelemetry-proto, trace/v1/trace.proto).


def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = bytearray()
    while n > 0x7F:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _field(number: int, wire: int, payload: bytes) -> bytes:
    return _varint(number << 3 | wire) + payload


def _bytes(number: int, data: bytes) -> bytes:
    return _field(number, 2, _varint(len(data)) + data)


def _any(value: Value) -> bytes:
    if isinstance(value, bool):
        return _field(2, 0, _varint(int(value)))
    if isinstance(value, int):
        return _field(3, 0, _varint(value))
    if isinstance(value, float):
        return _field(4, 1, struct.pack("<d", value))
    return _bytes(1, value.encode())


def _attributes(number: int, attrs: dict[str, Value]) -> bytes:
    return b"".join(
        _bytes(number, _bytes(1, key.encode()) + _bytes(2, _any(value)))
        for key, value in attrs.items()
    )


def _span(
    name: str,
    started: float,
    ended: float,
    ok: bool,
    attrs: dict[str, Value],
    ids: tuple[bytes, bytes, bytes | None] | None = None,
) -> bytes:
    """One span. ids: its trace, itself and its parent, for a span that is part of a tree;
    left out, it is a trace of its own."""
    trace_id, span_id, parent = ids or (os.urandom(16), os.urandom(8), None)
    return (
        _bytes(1, trace_id)
        + _bytes(2, span_id)
        + (_bytes(4, parent) if parent else b"")
        + _bytes(5, name.encode())
        + _field(6, 0, _varint(1))  # kind: internal
        + _field(7, 1, struct.pack("<Q", int(started * 1e9)))
        + _field(8, 1, struct.pack("<Q", int(max(started, ended) * 1e9)))
        + _attributes(9, attrs)
        + _bytes(15, _field(3, 0, _varint(1 if ok else 2)))  # status: ok or error
    )


def _request(spans: list[bytes], env: str) -> bytes:
    """One ExportTraceServiceRequest holding these spans."""
    resource = _attributes(1, {"service.name": "jap", "deployment.environment.name": env})
    scope = _bytes(1, _bytes(1, b"jap"))
    return _bytes(1, _bytes(1, resource) + _bytes(2, scope + b"".join(_bytes(2, s) for s in spans)))


_queue: queue.Queue[tuple[str, dict[str, str], str, bytes]] = queue.Queue(maxsize=MAX_QUEUE)
_lock = threading.Lock()
_thread: threading.Thread | None = None
_warned = False


def _warn(message: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        print(f"jap trace: {message}; traces are not being sent", file=sys.stderr)


def _headers(text: str) -> dict[str, str]:
    """JAP_TRACE_HEADERS, written as OpenTelemetry writes them: name=value,name=value."""
    pairs = (part.split("=", 1) for part in text.split(",") if "=" in part)
    return {k.strip(): v.strip() for k, v in pairs if k.strip() and v.strip()}


def target() -> tuple[str, dict[str, str]] | None:
    """Where traces go and with which headers, or None when tracing to a service is off."""
    settings = get_settings()
    url, headers = settings.jap_trace_endpoint.strip(), _headers(settings.jap_trace_headers)
    if not url or not headers:
        return None
    # A service gives its OTLP base address (LangSmith: .../otel); traces go to /v1/traces
    # under it. The full address is taken as it is.
    url = url.rstrip("/")
    url = url if url.endswith("/v1/traces") else url + "/v1/traces"
    parts = urlsplit(url)
    if parts.scheme != "https" and parts.hostname not in LOCAL_HOSTS:
        _warn("JAP_TRACE_ENDPOINT must be an https address")
        return None
    return url, headers


def _post(url: str, headers: dict[str, str], body: bytes, wait_s: float = WAIT_S) -> None:
    resp = httpx.post(
        url,
        content=body,
        headers={**headers, "Content-Type": "application/x-protobuf"},
        timeout=wait_s,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"the trace service answered {resp.status_code}")


def _run() -> None:
    while True:
        batch = [_queue.get()]
        while len(batch) < BATCH:
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break
        try:
            groups: dict[tuple[str, tuple[tuple[str, str], ...], str], list[bytes]] = {}
            for url, headers, env, span in batch:
                groups.setdefault((url, tuple(sorted(headers.items())), env), []).append(span)
            for (url, headers_, env), spans in groups.items():
                _post(url, dict(headers_), _request(spans, env))
        except Exception as exc:  # never let a trace take the work down
            _warn(f"{type(exc).__name__}: {str(exc)[:200]}")
        finally:
            for _ in batch:
                _queue.task_done()


def _ensure_thread() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="jap-trace-sender", daemon=True)
            _thread.start()


def _attrs(kind: Kind, fields: dict[str, Any]) -> dict[str, Value]:
    """A span's attributes: the fields the allow-list keeps, and what kind of work it was."""
    attrs = clean({"env": get_settings().jap_env, **fields})
    attrs["langsmith.span.kind"] = kind
    if kind == "llm":
        attrs["gen_ai.operation.name"] = "chat"
    if META + "thread_id" in attrs:
        attrs["session.id"] = attrs[META + "thread_id"]
    return attrs


def record(
    name: str, kind: Kind, started: float, ended: float, *, ok: bool = True, **fields: Any
) -> dict[str, Value]:
    """One span of AI work, from `started` to `ended` (clock seconds): queued for the trace
    service when one is set. Returns the attributes as they are sent, after the allow-list."""
    try:
        settings = get_settings()
        attrs = _attrs(kind, fields)
        to = target()
        if to is None or not NAME.match(name):
            return attrs
        _ensure_thread()
        try:
            _queue.put_nowait((*to, settings.jap_env, _span(name, started, ended, ok, attrs)))
        except queue.Full:
            _warn("the send queue is full")
        return attrs
    except Exception as exc:  # a trace must never fail the call it describes
        _warn(f"{type(exc).__name__}: {str(exc)[:200]}")
        return {}


def flush(timeout: float = 3.0) -> None:
    """Wait, a few seconds at most, until what is queued has been sent (a command about to
    exit, and tests)."""
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.02)


@dataclass(frozen=True)
class Node:
    """One span of a trace that is sent whole (send_tree). `path` names it within the trace
    and `parent` is the path of the span it belongs to; the span's id is worked out from it."""

    path: str
    parent: str | None
    name: str
    kind: Kind
    started: float
    ended: float
    ok: bool = True
    fields: dict[str, Any] = field(default_factory=dict)


class NotSent(RuntimeError):
    """The trace service did not take a trace; the caller keeps it and tries again."""


def _digest(text: str, size: int) -> bytes:
    return hashlib.sha256(text.encode()).digest()[:size]


def send_tree(key: str, nodes: Sequence[Node]) -> int | None:
    """One trace of many spans, sent now: how many spans the service took, or None when
    tracing to a service is off. Raises NotSent when it could not be sent (O22: the runner
    then keeps the run's trace for the next run). `key` names the trace, so the same key
    and paths give the same ids every time it is sent."""
    to = target()
    if to is None:
        return None
    url, headers = to
    env = get_settings().jap_env
    trace_id = _digest(f"jap:{key}", 16)
    spans = [
        _span(
            node.name,
            node.started,
            node.ended,
            node.ok,
            _attrs(node.kind, node.fields),
            (
                trace_id,
                _digest(f"jap:{key}/{node.path}", 8),
                _digest(f"jap:{key}/{node.parent}", 8) if node.parent is not None else None,
            ),
        )
        for node in nodes
        if NAME.match(node.name)
    ]
    try:
        for i in range(0, len(spans), TREE_BATCH):
            _post(url, headers, _request(spans[i : i + TREE_BATCH], env), TREE_WAIT_S)
    except Exception as exc:
        raise NotSent(f"{type(exc).__name__}: {str(exc)[:200]}") from None
    return len(spans)
