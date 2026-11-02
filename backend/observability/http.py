"""Every API request: a request id, one log line, and its time in route_stat (PRD O1, O4).

The request id comes from an X-Request-ID header when it looks safe, else a new one, and is
sent back in the response's X-Request-ID, so an error a user reports can be found in the
logs. The route is FastAPI's path template (/api/listings/{listing_id}), never the real
path, so ids in URLs don't split the counts. /api/inngest is Inngest calling the app: its
steps are timed by the Inngest middleware instead.
"""

import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from observability import context, writer
from observability.logs import event

log = logging.getLogger("jap.http")

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
QUIET_PATHS = ("/api/health",)  # polled every few seconds by the UI
SKIP_STATS = ("/api/inngest",)


def _request_id(scope: Scope) -> str:
    for name, value in scope.get("headers") or []:
        if name == b"x-request-id":
            rid = str(value.decode("latin-1"))
            if SAFE_ID.match(rid):
                return rid
    return uuid.uuid4().hex[:16]


def _route(scope: Scope) -> str:
    """The matched route's template with its router prefix: /api/listings/{listing_id}.

    FastAPI keeps an included router's routes without the prefix, so the prefix is the part
    of the real path in front of what the route's pattern matched."""
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    pattern = getattr(route, "path_regex", None)
    if not template:
        return "(no route)"
    path = str(scope.get("path", ""))
    if pattern is not None:
        for i in range(len(path) + 1):
            if path[i:].startswith("/") and pattern.match(path[i:]):
                return path[:i] + str(template)
    return str(template)


class RequestLog:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = _request_id(scope)
        token = context.push(request_id=rid)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers: list[Any] = list(message.get("headers") or [])
                headers.append((b"x-request-id", rid.encode()))
                message = {**message, "headers": headers}
            await send(message)

        failed = False
        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            failed = True
            raise
        finally:
            ms = int((time.perf_counter() - started) * 1000)
            self._done(scope, status, ms, failed)
            context.reset(token)

    def _done(self, scope: Scope, status: int, ms: int, failed: bool) -> None:
        method = str(scope.get("method", ""))
        path = str(scope.get("path", ""))
        route = _route(scope)
        if failed:
            status = 500
        if status >= 500:
            level = logging.ERROR
        elif path.startswith(QUIET_PATHS + SKIP_STATS):
            level = logging.DEBUG
        else:
            level = logging.INFO
        event(
            log,
            "http.request",
            f"{method} {route} {status} in {ms} ms",
            level=level,
            method=method,
            route=route,
            status=status,
            duration_ms=ms,
        )
        if not path.startswith(SKIP_STATS) and path.startswith("/api"):
            hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
            writer.route(hour, method, route, ms, status >= 500)
