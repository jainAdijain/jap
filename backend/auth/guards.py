"""Two request checks that run before any route (PRD S8, S9).

/api/inngest is how the Inngest server inside Docker runs JAP's functions. It answers only a
direct call from a private address with no proxy headers, so nothing that came through the
tunnel (or any other proxy) reaches it. A request that changes data must come from JAP's own
pages: when it carries an Origin, that Origin must be one JAP serves.
"""

import ipaddress
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from config import get_settings

INNGEST_PATH = "/api/inngest"
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "cf-connecting-ip", "x-real-ip", "cf-ray")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _private(host: str | None) -> bool:
    if not host:
        return False
    if host in ("localhost", "testclient", "test"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def internal_call(request: Request) -> bool:
    if any(h in request.headers for h in PROXY_HEADERS):
        return False
    return _private(request.client.host if request.client else None)


def _origin(url: str) -> str:
    parts = url.split("/")
    return "/".join(parts[:3]) if len(parts) >= 3 else url


def allowed_origins() -> set[str]:
    settings = get_settings()
    return {_origin(o).rstrip("/") for o in [*settings.cors_origins, settings.jap_ui_url]}


def _refuse(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if (path == INNGEST_PATH or path.startswith(INNGEST_PATH + "/")) and not internal_call(
            request
        ):
            return _refuse(404, "not_found", "Not Found")
        origin = request.headers.get("origin")
        if (
            request.method not in SAFE_METHODS
            and origin is not None
            and origin.rstrip("/") not in allowed_origins()
        ):
            return _refuse(403, "bad_origin", "This request did not come from JAP")
        return await call_next(request)
