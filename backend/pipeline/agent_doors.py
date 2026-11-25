"""Level 1 of the trace of AI work (PRD 1.33, O17): what JAP's doors gave out and took back.

The export and import commands (pipeline/claude_reader.py, pipeline/claude_writer.py) are
the only way a harness reaches JAP, so counting here covers every harness, also one driven
by hand with no runner. Each export and each import leaves one log_event row (agent.export
or agent.import, kept 30 days with the other events) and, when a trace service is set, one
span (observability/trace.py): how many items went out and how big the export was, how many
came back, how many were saved, and why the others were not, as codes from a fixed list.
Counts only: no id of a posting or an application, no title, no question, no answer.

From 1.38 (O26) an export or import the runner calls carries its run's stamp
(JAP_RUN_STAMP, from make ... RUN=<stamp>) and its span is sent inside that run's trace, under
the run, instead of as a trace of its own: one run is one trace, and the free plan's traces
a month are not spent on doors. Called by hand, with no stamp, it is a trace of its own.

The import's own lines ("<id>: not saved (missing: skills)") stay as they are for whoever
reads them; reason() turns each into its code. Nothing here may fail a door: record() and
done() swallow their own errors.
"""

import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from config import get_settings
from observability import trace, writer

# How the reader's import starts its reason for not saving (pipeline/claude_reader.py).
NOT_SAVED = (
    ("unknown or not wanted", "unknown_field"),
    ("missing", "missing_field"),
    ("not a posting id", "bad_id"),
    ("not an object", "bad_shape"),
    ("posting closed", "gone"),
    ("description changed", "changed"),
    ("already read", "already_read"),
    ("pair already", "pair_gone"),
    ("needs a pair id", "bad_pair"),
)
IDS = ("request_id", "user_id", "run_id", "application_id", "posting_id", "inngest_run_id")


def reason(line: str) -> str:
    """Why a line of the reader's import says an item was not saved, as a code."""
    why = line.partition("not saved (")[2]
    for starts, code in NOT_SAVED:
        if why.startswith(starts):
            return code
    return "bad_value" if ": " in why else "other"  # "<field>: <what the schema said>"


def reader_export(out: dict[str, Any], size: int) -> dict[str, int]:
    i, _, n = str(out.get("shard") or "1/1").partition("/")
    return {
        "postings": len(out["postings"]),
        "pairs": len(out["pairs"]),
        "unread": int(out.get("unread_to_read", out["unread_total"])),
        "shard": int(i),
        "shards": int(n),
        "bytes": size,
        "contract_version": int(out["contract_version"]),
    }


def reader_import(lines: list[str]) -> dict[str, int]:
    """The lines of import_fields (one per item, then the total) as counts."""
    items = lines[:-1]
    counts: Counter[str] = Counter(items=len(items))
    for line in items:
        if line.endswith((": merged", ": kept apart")):
            counts["merged" if line.endswith(": merged") else "kept_apart"] += 1
            counts["saved"] += 1
        elif line.endswith(": saved"):
            counts["saved"] += 1
        else:
            counts["not_saved"] += 1
            counts[f"not_saved_{reason(line)}"] += 1
    return dict(counts)


def writer_export(out: dict[str, Any], size: int) -> dict[str, int]:
    questions = [q for job in out["jobs"] for q in job["questions"]]
    return {
        "jobs": len(out["jobs"]),
        "questions": len(questions),
        "cover_letters": sum(bool(q.get("cover_letter")) for q in questions),
        "resumes": len(out["resumes"]),
        "bank_entries": len(out["answer_bank"]),
        "bytes": size,
        "contract_version": int(out["contract_version"]),
    }


def writer_import(answers: Any, lines: list[str]) -> dict[str, int]:
    """The lines of import_answers (one per job or resume) as counts; held is how many jobs
    came with the checker's "_review" (V6)."""
    given = answers.values() if isinstance(answers, dict) else []
    lines = [line for line in lines if not line.startswith("left out:")]  # W13's one line
    counts: Counter[str] = Counter(
        items=len(lines), held=sum(isinstance(a, dict) and bool(a.get("_review")) for a in given)
    )
    for line in lines:
        if line.startswith("resume:"):
            bad = "resume_not_read" if ": not read (" in line else ""
            counts["resumes_read"] += not bad
        else:
            bad = "not_answered" if ": not answered (" in line else ""
            counts["answered"] += not bad
        if bad:
            counts["not_saved"] += 1
            counts[f"not_saved_{bad}"] += 1
    counts["saved"] = counts["answered"] + counts["resumes_read"]
    return dict(counts)


def record(job: str, door: str, started: float, counts: dict[str, int]) -> None:
    """One export or import, as a log_event row and a span. `started` is clock seconds."""
    try:
        ended = time.time()
        ms = int((ended - started) * 1000)
        if get_settings().jap_log_db:
            said = ", ".join(f"{name} {n}" for name, n in counts.items())
            writer.log_row(
                {
                    "created_at": datetime.fromtimestamp(ended, UTC),
                    "level": "info",
                    "event": f"agent.{door}",
                    "message": f"AI {job} {door} in {ms} ms: {said}",
                    "logger": __name__,
                    **dict.fromkeys(IDS),
                    "fields": {"job": job, "duration_ms": ms, **counts},
                }
            )
        fields: dict[str, Any] = {"job": job, "door": door, "latency_ms": ms, **counts}
        stamp = os.environ.get("JAP_RUN_STAMP", "")
        ids = None
        if trace.STAMP.match(stamp):  # the runner's: inside its run's trace (agent_run_trace)
            ids = trace.within(f"{get_settings().jap_env}:{job}:{stamp}")
            fields["run_stamp"] = stamp
        name = f"door.{job}.{door}"
        trace.record(name, "chain", started, ended, mode="agent", ids=ids, **fields)
    except Exception as exc:  # the count is not worth a failed export or import
        print(f"jap trace: {type(exc).__name__}: {exc}", file=sys.stderr)


def done() -> None:
    """Before the command exits: what it printed is out, and its row and span are sent."""
    try:
        sys.stdout.flush()
        trace.flush()
        writer.flush()
    except Exception as exc:
        print(f"jap trace: {type(exc).__name__}: {exc}", file=sys.stderr)
