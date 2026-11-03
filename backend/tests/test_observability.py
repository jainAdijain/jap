"""Logging, observability and analytics (PRD 1.19, O1 to O12 and O15)."""

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import psycopg
import pytest

import llm.client as client
from database.session import get_sessionmaker
from functions.health_watch import find_step, send_step
from llm import call
from llm.redact import Sensitive
from observability import alerts, context, writer
from observability.inngest_mw import LogMiddleware
from observability.logs import ContextFilter, DbHandler, JsonFormatter, event
from observability.scrub import scrub, scrub_value
from observability.sentry import before_send
from pipeline import notify
from pipeline.apply import flow
from schemas.resume import ParsedResume
from tests.test_apply import _listing, _profile, _resume
from tests.test_llm_call import GOOD, Stub

APP_ID = "123e4567-e89b-12d3-a456-426614174000"


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.setFormatter(JsonFormatter())
        self.addFilter(ContextFilter())

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def captured() -> Any:
    log = logging.getLogger("jap.test.observability")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    cap = Capture()
    db = DbHandler(level=logging.INFO)
    db.addFilter(ContextFilter())
    log.handlers = [cap, db]
    yield log, cap
    log.handlers = []


# O2: redaction ---------------------------------------------------------------------------


def test_scrub_removes_secret_shapes_and_keeps_ids() -> None:
    text = (
        f"app {APP_ID} on 2026-11-02 12:30:45 for asha.rao@example.com, +91 98765 43210, "
        "9876543210, (415) 555-0134; Authorization: Bearer abcdefghijklmnop; "
        "key sk-or-v1-0123456789abcdef0123; password=hunter22 token: xyz123456; "
        "postgresql://jap:s3cret@db:5432/jap; Your security code is 482913"
    )
    out = scrub(text)
    for secret in (
        "asha.rao", "98765", "9876543210", "555-0134", "abcdefghijklmnop", "sk-or-v1",
        "hunter22", "xyz123456", "s3cret", "482913",
    ):  # fmt: skip
        assert secret not in out, secret
    assert APP_ID in out and "2026-11-02 12:30:45" in out


def test_scrub_value_drops_personal_fields() -> None:
    out = scrub_value(
        {
            "answers": {"q": "yes"},
            "resume_text": "...",
            "password": "x",
            "count": 3,
            "note": "mail me@example.com",
        }  # fmt: skip
    )
    assert out == {
        "answers": "[REDACTED]",
        "resume_text": "[REDACTED]",
        "password": "[REDACTED]",
        "count": 3,
        "note": "mail [EMAIL]",
    }


# O1: one format with ids ------------------------------------------------------------------


def test_json_line_carries_context_ids(captured: Any) -> None:
    log, cap = captured
    token = context.push(request_id="req-12345678")
    try:
        with context.scope(application_id=APP_ID, run_id=None):
            event(log, "apply.test", "Hello from asha.rao@example.com", answers="secret", n=2)
        event(log, "after", "outside the scope")
    finally:
        context.reset(token)
    first, second = (json.loads(line) for line in cap.lines)
    assert first["event"] == "apply.test" and first["level"] == "info"
    assert first["request_id"] == "req-12345678" and first["application_id"] == APP_ID
    assert "run_id" not in first
    assert first["msg"] == "Hello from [EMAIL]"
    assert first["fields"] == {"answers": "[REDACTED]", "n": 2}
    assert "application_id" not in second and second["request_id"] == "req-12345678"


def test_exception_is_logged_with_type_and_scrubbed(captured: Any) -> None:
    log, cap = captured
    try:
        raise ValueError("bad login for me@example.com password=abc12345")
    except ValueError:
        log.exception("boom")
    line = json.loads(cap.lines[0])
    assert line["exc_type"] == "ValueError" and "abc12345" not in line["exc"]
    assert "me@example.com" not in line["exc"]


# O3: warnings, errors and timeline steps are stored ----------------------------------------


async def test_db_keeps_warnings_errors_and_keep_lines(
    db: str, sql: psycopg.Connection[Any], captured: Any
) -> None:
    log, _ = captured
    with context.scope(application_id=APP_ID):
        event(log, "plain.info", "not stored")
        event(log, "step.info", "stored for the timeline", keep=True)
        log.warning("a warning with token=abcdef123456")
        event(log, "x.failed", "an error", level=logging.ERROR, attempt=2)
    writer.flush()
    rows = sql.execute(
        "SELECT level, event, message, application_id::text, fields FROM log_event ORDER BY id"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        ("info", "step.info"),
        ("warning", "jap.test.observability"),
        ("error", "x.failed"),
    ]
    assert all(r[3] == APP_ID for r in rows)
    assert "abcdef123456" not in rows[1][2] and rows[2][4] == {"attempt": 2}


# O4: requests ----------------------------------------------------------------------------


async def test_requests_get_an_id_and_are_timed(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    resp = await api.get("/api/insights")
    assert resp.status_code == 200 and len(resp.headers["x-request-id"]) >= 8
    mine = await api.get("/api/insights", headers={"X-Request-ID": "trace-me-12345"})
    assert mine.headers["x-request-id"] == "trace-me-12345"
    bad = await api.get("/api/insights", headers={"X-Request-ID": "no spaces <script>"})
    assert bad.headers["x-request-id"] != "no spaces <script>"
    await api.get(f"/api/applications/{uuid.uuid4()}")  # 404, counted under the template
    writer.flush()
    rows = dict(sql.execute("SELECT route, count FROM route_stat WHERE method = 'GET'").fetchall())
    assert rows["/api/insights"] == 3 and rows["/api/applications/{app_id}"] == 1


# O5: Status ------------------------------------------------------------------------------


async def test_status_shows_stuck_runs_errors_and_missed_daily_search(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    sql.execute(
        "INSERT INTO profile (email, daily_run, daily_run_at, updated_at)"
        " VALUES ('me@example.com', true, '00:00', now() - interval '2 days')"
    )
    sql.execute(
        "INSERT INTO run (trigger, mode, status, started_at) VALUES"
        " ('manual', 'find_only', 'running', now() - interval '45 minutes'),"
        " ('manual', 'find_only', 'running', now() - interval '5 minutes')"
    )
    for i in range(3):
        sql.execute(
            "INSERT INTO log_event (level, event, message, logger) VALUES"
            " ('error', 'fetch.failed', %s, 'x')",
            (f"board {i} failed",),
        )
    sql.execute(
        "INSERT INTO log_event (level, event, message, logger, created_at) VALUES"
        " ('error', 'old', 'yesterday', 'x', now() - interval '2 days')"
    )
    body = (await api.get("/api/ops/status")).json()
    assert len(body["stuck_runs"]) == 1
    assert body["errors_24h"] == 3
    assert body["error_groups"][0]["event"] == "fetch.failed"
    assert body["error_groups"][0]["count"] == 3
    assert body["error_groups"][0]["last_message"] == "board 2 failed"
    now_local = datetime.now(UTC).astimezone(context_tz())
    assert body["daily"]["on"] is True
    assert body["daily"]["missed"] is (now_local.hour * 60 + now_local.minute >= 30)
    assert body["disk"]["total_bytes"] > 0 and "spend" in body


def context_tz() -> Any:
    from zoneinfo import ZoneInfo

    from config import get_settings

    return ZoneInfo(get_settings().jap_tz)


# O6: alerts ------------------------------------------------------------------------------


async def test_health_alerts_are_sent_once(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[notify.Mail] = []

    async def fake_send(mail: notify.Mail) -> None:
        sent.append(mail)

    monkeypatch.setattr(notify, "send", fake_send)
    sql.execute("INSERT INTO profile (email, spend_cap_usd) VALUES ('me@example.com', 1.00)")
    sql.execute(
        "INSERT INTO llm_call (purpose, model, prompt_version, input_sha256, cost_usd)"
        " VALUES ('answer', 'm', 'v1', 'x', 0.85)"
    )
    sql.execute(
        "INSERT INTO run (trigger, mode, status, started_at) VALUES"
        " ('manual', 'find_only', 'running', now() - interval '2 hours')"
    )
    for i in range(12):
        sql.execute(
            "INSERT INTO log_event (level, event, message, logger) VALUES"
            " ('error', 'boom', %s, 'x')",
            (f"error {i}",),
        )
    due = await find_step()
    keys = sorted(a["key"].split(":")[0] for a in due)
    assert keys == ["errors", "spend-80", "stuck-run"]
    for alert in due:
        assert await send_step(alert) == "sent"
        assert await send_step(alert) == "already sent"
    assert await find_step() == []
    assert len(sent) == 3 and all(m.to == "me@example.com" for m in sent)
    spend = next(m for m in sent if "AI limit" in m.subject)
    assert spend.subject.startswith("[DEV] JAP alert: 85%")
    assert "/#settings" in spend.body


async def test_cleanup_keeps_30_days(db: str, sql: psycopg.Connection[Any]) -> None:
    sql.execute(
        "INSERT INTO log_event (level, event, message, logger, created_at) VALUES"
        " ('error', 'old', 'm', 'x', now() - interval '31 days'),"
        " ('error', 'new', 'm', 'x', now() - interval '29 days')"
    )
    sql.execute(
        "INSERT INTO route_stat (hour, method, route) VALUES"
        " (now() - interval '40 days', 'GET', '/a'), (now(), 'GET', '/a')"
    )
    async with get_sessionmaker()() as session:
        done = await alerts.cleanup(session)
    assert done["log_events"] == 1 and done["route_stats"] == 1
    assert sql.execute("SELECT event FROM log_event").fetchall() == [("new",)]


# O8: application timeline ----------------------------------------------------------------


async def test_timeline_lists_steps_and_model_calls(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _profile(sql)
    _resume(sql, tmp_path)
    listing = _listing(sql)
    token = context.push(request_id="t-12345678")
    try:
        async with get_sessionmaker()() as session:
            app_id = await flow.create_attempt(session, listing, None)
            monkeypatch.setattr(client, "chat", Stub([GOOD]))
            await call(
                session, "resume_parse", {"resume_text": "Python"}, ParsedResume,
                sensitive=Sensitive(),
            )  # fmt: skip
            await flow.fail_by_id(session, app_id, "The form page answered 404")
    finally:
        context.reset(token)
    writer.flush()
    body = (await api.get(f"/api/applications/{app_id}/timeline")).json()
    events = [i["event"] for i in body["items"]]
    assert events[0] == "created"
    assert "apply.created" in events and "apply.failed" in events
    assert "llm.resume_parse" in events
    model = next(i for i in body["items"] if i["kind"] == "model")
    assert model["cost_usd"] == 0.001
    assert body["status"] == "failed" and body["kept_days"] == 30
    missing = await api.get(f"/api/applications/{uuid.uuid4()}/timeline")
    assert missing.status_code == 404


async def test_inngest_middleware_binds_ids_and_logs_steps(
    db: str, sql: psycopg.Connection[Any]
) -> None:
    token = context.push()
    try:
        mw = LogMiddleware(SimpleNamespace(), None)  # type: ignore[arg-type]
        ctx = SimpleNamespace(run_id="01INNGEST", event=SimpleNamespace(data={"run_id": APP_ID}))
        await mw.transform_input(ctx, SimpleNamespace(id="jap-fetch-board"), None)
        ids = context.current()
        assert ids["inngest_run_id"] == "01INNGEST" and ids["run_id"] == APP_ID
        assert ids["function"] == "jap-fetch-board"
        step = SimpleNamespace(id="fetch", op=SimpleNamespace(value="StepRun"))
        await mw.transform_output(SimpleNamespace(error=None, step=step))
        await mw.transform_output(SimpleNamespace(error=RuntimeError("HTTP 500"), step=step))
    finally:
        context.reset(token)
    writer.flush()
    rows = sql.execute("SELECT level, event, inngest_run_id FROM log_event ORDER BY id").fetchall()
    assert rows == [
        ("info", "inngest.step", "01INNGEST"),
        ("warning", "inngest.step.failed", "01INNGEST"),
    ]


# O10 to O12: Insights --------------------------------------------------------------------


async def test_insights_funnel_sources_and_spend(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any], tmp_path: Path
) -> None:
    _profile(sql)
    resume = _resume(sql, tmp_path)
    a = _listing(sql, "Backend Engineer", "1")
    b = _listing(sql, "Data Engineer", "2", company="Beta")
    sql.execute("INSERT INTO listing (company_id, canonical_role, title_display)"
                " SELECT company_id, 'x', 'Unmatched' FROM listing LIMIT 1")  # fmt: skip
    for listing, status, error in ((a, "applied", None), (b, "failed", "Form changed")):
        sql.execute(
            "INSERT INTO application (listing_id, posting_id, status, mode, resume_version_id,"
            " submitted_at, error) SELECT id, primary_posting_id, %s, 'review', %s,"
            " CASE WHEN %s = 'applied' THEN now() END, %s FROM listing WHERE id = %s",
            (status, resume, status, error, listing),
        )
    sql.execute(
        "INSERT INTO llm_call (purpose, model, prompt_version, input_sha256, cost_usd) VALUES"
        " ('answer', 'm1', 'v1', 'x', 0.02), ('answer', 'm1', 'v1', 'x', 0.01),"
        " ('extract', 'm2', 'v1', 'x', 0.005)"
    )
    body = (await api.get("/api/insights", params={"weeks": 4})).json()
    assert len(body["funnel"]) == 4
    assert body["totals"] == {
        "week": "total", "found": 3, "matched": 2, "prepared": 2, "applied": 1, "failed": 1,
    }  # fmt: skip
    gh = body["sources"][0]
    assert gh["source"] == "greenhouse" and gh["applied"] == 1 and gh["failed"] == 1
    assert gh["success_rate"] == 0.5 and gh["top_failures"] == ["Form changed (1)"]
    assert body["spend_this_month"][0] == {
        "purpose": "answer", "model": "m1", "calls": 2, "cost_usd": 0.03,
    }  # fmt: skip
    month = body["months"][-1]
    assert month["applied"] == 1 and month["per_application_usd"] == 0.035
    assert len(body["months"]) == 6


# O15: Sentry ----------------------------------------------------------------------------


def test_sentry_events_are_scrubbed() -> None:
    event_in: dict[str, Any] = {
        "request": {
            "url": "https://jap.example/api/x?token=abc",
            "cookies": {"jap_session": "s"},
            "headers": {"Authorization": "Bearer abcdefghijkl"},
            "data": {"password": "p"},
        },
        "user": {"email": "me@example.com"},
        "exception": {
            "values": [
                {
                    "value": "failed for me@example.com",
                    "stacktrace": {"frames": [{"vars": {"answer": "x"}, "context_line": "y"}]},
                }
            ]
        },
        "logentry": {"message": "code is 123456", "params": ["a"]},
        "extra": {"resume_text": "...", "n": 1},
    }
    out = before_send(event_in)
    assert out["request"] == {"url": "https://jap.example/api/x"}
    assert "user" not in out
    exc = out["exception"]["values"][0]
    assert exc["value"] == "failed for [EMAIL]" and exc["stacktrace"]["frames"] == [{}]
    assert out["logentry"] == {"message": "code is [SECRET]"}
    assert out["extra"] == {"resume_text": "[REDACTED]", "n": 1}


def test_ids_outside_a_context_are_empty() -> None:
    token = context.push()
    try:
        assert context.current() == {} and context.uuid_of("run_id") is None
        context.bind(run_id="not-a-uuid", unknown="x")
        assert context.current() == {"run_id": "not-a-uuid"}
        assert context.uuid_of("run_id") is None
    finally:
        context.reset(token)
