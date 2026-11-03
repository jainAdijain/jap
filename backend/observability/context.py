"""The ids a log line belongs to (PRD O1), set once per request or Inngest call.

bind() adds ids to the current context; every log line written afterwards in the same
request, Inngest call or step carries them, without passing them around. Each request and
each Inngest call runs in its own asyncio task, so ids never leak from one to another.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

# The ids a line may carry, in the order they print.
IDS = (
    "request_id",
    "user_id",
    "run_id",
    "application_id",
    "posting_id",
    "listing_id",
    "inngest_run_id",
    "function",
)

_ctx: ContextVar[dict[str, str]] = ContextVar("jap_log_context", default={})  # noqa: B039


def _clean(ids: dict[str, Any]) -> dict[str, str]:
    return {k: str(v) for k, v in ids.items() if k in IDS and v not in (None, "")}


def bind(**ids: Any) -> None:
    """Add ids to the current context (None and unknown names are ignored)."""
    _ctx.set({**_ctx.get(), **_clean(ids)})


def push(**ids: Any) -> Token[dict[str, str]]:
    """Start a fresh context with only these ids; reset() it when done."""
    return _ctx.set(_clean(ids))


def reset(token: Token[dict[str, str]]) -> None:
    _ctx.reset(token)


@contextmanager
def scope(**ids: Any) -> Iterator[None]:
    """These ids added for the duration of a with block."""
    token = _ctx.set({**_ctx.get(), **_clean(ids)})
    try:
        yield
    finally:
        _ctx.reset(token)


def current() -> dict[str, str]:
    return dict(_ctx.get())


def uuid_of(name: str) -> uuid.UUID | None:
    """An id from the context as a UUID, or None when it is unset or not a UUID."""
    value = _ctx.get().get(name)
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
