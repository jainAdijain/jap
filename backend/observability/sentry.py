"""Errors to Sentry's free Developer plan (PRD O15), only when SENTRY_DSN is set.

Only errors are sent: no performance tracing, no request bodies, cookies, headers or local
variables, and every message and string in the event is scrubbed first. JAP works the same
with it off; dev leaves SENTRY_DSN empty.
"""

import logging
from typing import Any

from config import get_settings
from observability.scrub import scrub, scrub_value

log = logging.getLogger(__name__)

DROP_REQUEST = ("cookies", "data", "headers", "query_string", "env")


def before_send(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """The event as it may leave JAP: request details dropped, every string scrubbed."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in DROP_REQUEST:
            request.pop(key, None)
        if "url" in request:
            request["url"] = scrub(str(request["url"]).split("?", 1)[0])
    event.pop("user", None)
    event.pop("server_name", None)
    for exc in (event.get("exception") or {}).get("values") or []:
        if "value" in exc:
            exc["value"] = scrub(str(exc["value"]))
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
            frame.pop("pre_context", None)
            frame.pop("context_line", None)
            frame.pop("post_context", None)
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        logentry["message"] = scrub(str(logentry.get("message", "")))
        logentry.pop("params", None)
    if "message" in event:
        event["message"] = scrub(str(event["message"]))
    for key in ("extra", "contexts", "tags", "breadcrumbs"):
        if key in event:
            event[key] = scrub_value(event[key])
    return event


def setup_sentry() -> bool:
    """Start the Sentry client when SENTRY_DSN is set. True when it is on."""
    settings = get_settings()
    if not settings.sentry_dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.jap_env,
        send_default_pii=False,
        include_local_variables=False,
        include_source_context=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        max_breadcrumbs=0,
        before_send=before_send,  # type: ignore[arg-type]
        integrations=[LoggingIntegration(level=None, event_level=logging.ERROR)],
    )
    log.info("Sentry is on for %s", settings.jap_env)
    return True
