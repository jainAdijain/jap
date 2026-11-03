"""Writes log events and request timings to Postgres off the request path (PRD O3, O4).

A logging call or a finished request only puts a row on a queue; one background thread
inserts it with its own plain psycopg connection. If the database can't be reached, rows
are dropped (the JSON line on stdout still has them) and one line is printed to stderr.
The thread never logs through `logging`, so a failed write can't loop back into itself.
"""

import json
import queue
import sys
import threading
from datetime import datetime
from typing import Any

import psycopg

from config import get_settings

MAX_QUEUE = 10_000

LOG_SQL = """
INSERT INTO log_event (created_at, level, event, message, logger, request_id, user_id, run_id,
                       application_id, posting_id, inngest_run_id, fields)
VALUES (%(created_at)s, %(level)s, %(event)s, %(message)s, %(logger)s, %(request_id)s,
        %(user_id)s, %(run_id)s, %(application_id)s, %(posting_id)s, %(inngest_run_id)s,
        %(fields)s)
"""

ROUTE_SQL = """
INSERT INTO route_stat (hour, method, route, count, errors, total_ms, max_ms)
VALUES (%(hour)s, %(method)s, %(route)s, 1, %(errors)s, %(ms)s, %(ms)s)
ON CONFLICT (hour, method, route) DO UPDATE SET
    count = route_stat.count + 1,
    errors = route_stat.errors + EXCLUDED.errors,
    total_ms = route_stat.total_ms + EXCLUDED.total_ms,
    max_ms = GREATEST(route_stat.max_ms, EXCLUDED.max_ms)
"""

_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=MAX_QUEUE)
_lock = threading.Lock()
_thread: threading.Thread | None = None
_warned = False


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _warn(message: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        print(f"jap observability: {message}; log rows are not being stored", file=sys.stderr)


def _run() -> None:
    conn: psycopg.Connection[Any] | None = None
    url = ""
    while True:
        kind, row = _queue.get()
        try:
            wanted = _libpq(get_settings().database_url)
            if conn is None or conn.closed or url != wanted:
                if conn is not None and not conn.closed:
                    conn.close()
                url = wanted
                conn = psycopg.connect(url, autocommit=True, connect_timeout=3)
            if kind == "log":
                conn.execute(LOG_SQL, {**row, "fields": json.dumps(row["fields"], default=str)})
            elif kind == "route":
                conn.execute(ROUTE_SQL, row)
        except Exception as exc:  # never let a log write take the app down
            _warn(f"{type(exc).__name__}: {exc}")
            if conn is not None and not conn.closed:
                conn.close()
            conn = None
        finally:
            _queue.task_done()


def _ensure_thread() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="jap-log-writer", daemon=True)
            _thread.start()


def _put(kind: str, row: dict[str, Any]) -> None:
    _ensure_thread()
    try:
        _queue.put_nowait((kind, row))
    except queue.Full:
        _warn("the write queue is full")


def log_row(row: dict[str, Any]) -> None:
    _put("log", row)


def route(hour: datetime, method: str, route: str, ms: int, error: bool) -> None:
    _put(
        "route",
        {"hour": hour, "method": method, "route": route, "ms": ms, "errors": int(error)},
    )


def flush() -> None:
    """Wait until every queued row is written (tests, and before a shutdown)."""
    if _thread is not None and _thread.is_alive():
        _queue.join()
