"""The one module that talks to the trace service (PRD 1.33, O16, O22, O23).

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
"""

import os
import queue
import re
import struct
import sys
import threading
import time
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from config import get_settings

Kind = Literal["llm", "chain"]
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
)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "host.docker.internal")
MAX_QUEUE = 1_000
BATCH = 50
WAIT_S = 5.0


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


# Every field a trace may carry: the name it is sent under, and what its value must be.
FIELDS: dict[str, tuple[str, Check]] = {
    "model": ("gen_ai.request.model", _name),
    "model_served": ("gen_ai.response.model", _name),
    "provider": ("gen_ai.system", _name),
    "max_tokens": ("gen_ai.request.max_tokens", _count),
    "tokens_in": ("gen_ai.usage.input_tokens", _count),
    "tokens_out": ("gen_ai.usage.output_tokens", _count),
    "tokens_cached": ("gen_ai.usage.cache_read.input_tokens", _count),
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
    **{name: (META + name, _count) for name in COUNTS},
    **{f"not_saved_{why}": (f"{META}not_saved_{why}", _count) for why in REASONS},
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


def _span(name: str, started: float, ended: float, ok: bool, attrs: dict[str, Value]) -> bytes:
    return (
        _bytes(1, os.urandom(16))  # trace_id
        + _bytes(2, os.urandom(8))  # span_id
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


def _post(url: str, headers: dict[str, str], body: bytes) -> None:
    resp = httpx.post(
        url,
        content=body,
        headers={**headers, "Content-Type": "application/x-protobuf"},
        timeout=WAIT_S,
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


def record(
    name: str, kind: Kind, started: float, ended: float, *, ok: bool = True, **fields: Any
) -> dict[str, Value]:
    """One span of AI work, from `started` to `ended` (clock seconds): queued for the trace
    service when one is set. Returns the attributes as they are sent, after the allow-list."""
    try:
        settings = get_settings()
        attrs = clean({"env": settings.jap_env, **fields})
        attrs["langsmith.span.kind"] = kind
        if kind == "llm":
            attrs["gen_ai.operation.name"] = "chat"
        if META + "thread_id" in attrs:
            attrs["session.id"] = attrs[META + "thread_id"]
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
