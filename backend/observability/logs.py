"""One log format for the app and every Inngest function (PRD O1 to O3).

Each line is one JSON object on stdout: time, level, event, message, the ids in context
(observability/context.py), extra fields, and a traceback when there is one, all scrubbed
(observability/scrub.py). Docker keeps stdout, capped per environment (infra/compose.yaml).
Warnings and errors, and lines marked keep=True (application timeline steps), also go to the
log_event table.

Write an event with event(); a plain log.info()/log.warning() works too and uses the logger
name as its event.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from config import get_settings
from observability import context, writer
from observability.scrub import scrub, scrub_value

# Loggers whose lines never go to the database: the writer's own driver, and SQL echo.
NOT_STORED = ("psycopg", "sqlalchemy", "observability.writer", "alembic")
QUIET = {"httpx": logging.WARNING, "httpcore": logging.WARNING, "uvicorn.access": logging.WARNING}

# uvicorn's own lines (startup, shutdown) come from a logger called uvicorn.error.
RENAMED = {"uvicorn.error": "uvicorn"}

_installed = False


def event(
    log: logging.Logger,
    name: str,
    message: str,
    *,
    level: int = logging.INFO,
    keep: bool = False,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    """Write one event line: log.info("...") with a stable event name and fields."""
    log.log(
        level, message, extra={"event": name, "fields": fields, "keep": keep}, exc_info=exc_info
    )


def entry(record: logging.LogRecord) -> dict[str, Any]:
    """The record as the dict that is printed and stored (scrubbed)."""
    out: dict[str, Any] = {
        "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
        "level": record.levelname.lower(),
        "event": str(getattr(record, "event", None) or RENAMED.get(record.name, record.name)),
        "msg": scrub(record.getMessage()),
        "logger": record.name,
        "env": get_settings().jap_env,
    }
    out.update(getattr(record, "jap_ids", None) or {})
    fields = getattr(record, "fields", None)
    if fields:
        out["fields"] = scrub_value(fields)
    if record.exc_info and record.exc_info[0] is not None:
        out["exc"] = scrub(logging.Formatter().formatException(record.exc_info))
        out["exc_type"] = record.exc_info[0].__name__
    return out


class ContextFilter(logging.Filter):
    """Copies the ids in context onto the record where it is created (before any queue)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "jap_ids"):
            record.jap_ids = context.current()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(entry(record), default=str, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    """LOG_JSON=false: one readable line, the same content."""

    def format(self, record: logging.LogRecord) -> str:
        e = entry(record)
        ids = " ".join(f"{k}={e[k]}" for k in context.IDS if k in e)
        fields = " ".join(f"{k}={v}" for k, v in (e.get("fields") or {}).items())
        line = f"{e['ts']} {e['level'].upper():7} {e['event']}: {e['msg']}"
        line += f"  {ids}" if ids else ""
        line += f"  {fields}" if fields else ""
        return line + (f"\n{e['exc']}" if "exc" in e else "")


class DbHandler(logging.Handler):
    """Warnings, errors and keep=True lines to the log_event table, through the writer."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(NOT_STORED):
            return
        if record.levelno < logging.WARNING and not getattr(record, "keep", False):
            return
        try:
            e = entry(record)
            fields = dict(e.get("fields") or {})
            if "exc" in e:
                fields["exc"] = e["exc"][-4000:]
                fields["exc_type"] = e["exc_type"]
            if "listing_id" in e:
                fields.setdefault("listing_id", e["listing_id"])
            if "function" in e:
                fields.setdefault("function", e["function"])
            writer.log_row(
                {
                    "created_at": datetime.fromtimestamp(record.created, UTC),
                    "level": e["level"],
                    "event": e["event"][:200],
                    "message": e["msg"][:2000],
                    "logger": e["logger"],
                    "request_id": e.get("request_id"),
                    "user_id": e.get("user_id"),
                    "run_id": e.get("run_id"),
                    "application_id": e.get("application_id"),
                    "posting_id": e.get("posting_id"),
                    "inngest_run_id": e.get("inngest_run_id"),
                    "fields": fields,
                }
            )
        except Exception:
            self.handleError(record)


def setup_logging() -> None:
    """Send every logger through the JSON (or plain) stdout handler and the DB handler."""
    global _installed
    settings = get_settings()
    root = logging.getLogger()
    if _installed:
        root.setLevel(settings.log_level.upper())
        return
    _installed = True
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(JsonFormatter() if settings.log_json else PlainFormatter())
    stdout.addFilter(ContextFilter())
    handlers: list[logging.Handler] = [stdout]
    if settings.jap_log_db:
        db = DbHandler(level=logging.INFO)
        db.addFilter(ContextFilter())
        handlers.append(db)
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(settings.log_level.upper())
    # uvicorn sets up its own handlers before the app is imported; route them through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "inngest"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    for name, level in QUIET.items():
        logging.getLogger(name).setLevel(level)
