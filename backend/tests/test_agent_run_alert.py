"""An email when an AI run breaks or does nothing (PRD 1.35, O25)."""

import io
import json
import sys
import time
from pathlib import Path
from typing import Any

import psycopg
import pytest

from config import get_settings
from pipeline import agent_run_alert, agent_run_trace, notify

RUNS = Path(__file__).parent / "traces" / "agent_runs.json"
NOW = time.time()


def _run(job: str = "reader", **changes: Any) -> dict[str, Any]:
    """A run's trace file as the runner writes it (the backend's copy of two real shapes)."""
    reader, written = json.loads(RUNS.read_text())
    run = dict(reader if job == "reader" else written)
    run.update(started=NOW - 600, ended=NOW - 60)
    run.update(changes)
    return run


def _broken_morning() -> dict[str, Any]:
    """The reader of 25 Nov 2026: every export lost to a Mac asleep, then the plan's limit."""
    return _run(
        exit=1,
        before={"run_active": 0, "unread": 622, "pairs": 0},
        after={"run_active": 0, "unread": 622, "pairs": 0},
        totals={
            "sessions": 9,
            "failed": 9,
            "saved": 0,
            "causes": {"slept": 8, "usage_limit": 1, "a cause no one knows": 3},
        },
    )


def test_a_run_that_failed_says_how_in_counts_and_what_to_do() -> None:
    alert = agent_run_alert.judge(_broken_morning(), NOW)
    assert alert is not None
    assert alert.subject == "JAP alert: the AI reader run failed"
    assert alert.key.startswith("agent-run:reader:")
    body = "\n".join(alert.lines)
    assert "Sessions: 9, of them failed: 9 (slept 8, usage_limit 1)." in body
    assert "Saved 0 postings. Unread before: 622, after: 622." in body
    assert agent_run_alert.WHAT_TO_DO["slept"] in body
    assert agent_run_alert.WHAT_TO_DO["usage_limit"] in body
    assert "a cause no one knows" not in body
    assert f"agent/runs/{_run()['stamp']}-reader.log" in body


@pytest.mark.parametrize(
    ("run", "subject"),
    [
        (_run(exit=75), "JAP alert: the AI reader run stopped at the harness's usage limit"),
        (
            _run(exit=0, before={"unread": 5, "pairs": 0}, totals={"sessions": 2, "saved": 0}),
            "JAP alert: the AI reader run saved nothing",
        ),
        (
            _run("writer", exit=1, cause="no_answer", totals={}),
            "JAP alert: the AI writer run failed",
        ),
    ],
)
def test_a_run_that_stopped_or_did_nothing_sends_one(run: dict[str, Any], subject: str) -> None:
    alert = agent_run_alert.judge(run, NOW)
    assert alert is not None and alert.subject == subject


@pytest.mark.parametrize(
    "run",
    [
        _run(exit=0),  # it saved what it read
        _run(exit=0, before={"unread": 0, "pairs": 0}, totals={"sessions": 0, "saved": 0}),
        _run("writer", exit=0),  # a writer that left jobs out did its work
        _run(exit=1, ended=NOW - 40 * 3600),  # a trace that waited too long to be news
        {"job": "reader", "exit": 1},  # no time
        {"job": "nosuch", "exit": 1, "ended": NOW},
        "not a run",
    ],
)
def test_a_run_that_did_its_work_or_is_old_sends_none(run: Any) -> None:
    assert agent_run_alert.judge(run, NOW) is None


async def test_one_email_a_day_for_each_job(
    db: str, sql: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hourly tries of a broken morning (AH6) send one email, not one each."""
    sent: list[notify.Mail] = []

    async def fake_send(mail: notify.Mail) -> None:
        sent.append(mail)

    monkeypatch.setattr(notify, "send", fake_send)
    monkeypatch.setenv("JAP_ALERTS_ENABLED", "true")
    get_settings.cache_clear()
    sql.execute("INSERT INTO profile (email) VALUES ('me@example.com')")
    assert await agent_run_alert.send(_broken_morning()) == "sent"
    assert await agent_run_alert.send(_run(exit=75)) == "already sent"
    assert await agent_run_alert.send(_run("writer", exit=75)) == "sent"
    assert await agent_run_alert.send(_run(exit=0)) == "none"
    assert [m.to for m in sent] == ["me@example.com"] * 2
    assert "slept 8" in sent[0].body and "/#settings" in sent[0].body


async def test_alerts_off_sends_none(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAP_ALERTS_ENABLED", "false")
    get_settings.cache_clear()
    assert await agent_run_alert.send(_broken_morning()) == "alerts are off"


def test_the_trace_door_still_says_one_line_when_the_email_cannot_go(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The runner reads the door's last line; an alert that fails must not change it."""

    async def broken(run: Any) -> str:
        raise RuntimeError("SMTP is down")

    monkeypatch.setattr(agent_run_alert, "send", broken)
    monkeypatch.setenv("JAP_TRACE_ENDPOINT", "")
    get_settings.cache_clear()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_broken_morning())))
    assert agent_run_trace.main() == 0
    said = capsys.readouterr()
    assert said.out == "off\n"
    assert "jap alert: RuntimeError: SMTP is down" in said.err
