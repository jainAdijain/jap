"""The runner's trace of a run, on its way to the trace service (PRD 1.33, O22; 1.34, O24).

agent/run.sh works on the Mac beside the harness and must never hold the trace service's key
(AH8), so it cannot send its own trace. It hands the run's trace file to this command, the
third of JAP's doors for the runner after export and import:

    python -m pipeline.agent_run_trace < <data>/agent/traces/<run>-<job>.json
    (make agent-trace-send FILE=...)

and reads the one line it prints: "sent <n> spans", "off" (no trace service is set), "not a
run trace" (nothing worth trying again), or "not sent (<why>)" with exit code 1. On that last
one, and when JAP does not answer at all, the runner keeps the file marked and tries again
at the start of its next run.

A run becomes one trace. The run is the root span; under it one span per harness session,
with its export and its import as steps of their own (O18); under a session one span per
model request (tokens in and out, cached) and per tool call (the tool, the file of the
batch folder, whether it worked), where the harness gave a record (O17, level 3). Tokens go
out as gen_ai.usage only on a model request, so a service that adds a trace up counts them
once; a session and a run carry their totals under names of their own, and a session with
no record carries its estimate under a name that says it is one.

The file was written by agent/trace.py and holds numbers, ids and names from short lists.
This door does not rely on that. Nothing is copied from the file into a span except through
observability.trace's allow-list, which keeps a value only when its name is on FIELDS and
the value is what that name allows: a file that held text would send none of it
(tests/test_trace.py). No id of an application or a posting is sent, only counts.
"""

import json
import sys
from typing import Any

from config import get_settings
from observability import trace

VERSION = 1  # of the trace file (agent/trace.py)
MAX_SPANS = 10_000
# What the reader's import did not save, where the runner's word differs from the door's.
NOT_SAVED = {"invalid_value": "bad_value"}
READ = ("postings", "pairs", "written", "saved", "merged", "kept_apart")
WRITTEN = (
    "jobs",
    "questions",
    "resumes",
    "written",
    "held",
    "resumes_written",
    "left_out",
    "not_answered",
    "resumes_read",
)
SESSION_TOTALS = ("model_requests", "tool_calls", "turns", "model_ms", "tool_ms", "cost_usd")
RUN_TOTALS = (
    "sessions",
    "failed",
    "traced",
    "model_requests",
    "tool_calls",
    "model_ms",
    "tool_ms",
    "input_changed",
    "cost_usd",
    "denied",
    "saved",
    "pairs_settled",
    "left_out",
    "reasons_dropped",
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _when(value: Any) -> float | None:
    """A time in the file as clock seconds, or None when it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 1e9 < value < 4e9 else None


def _whole(value: Any) -> Any:
    """A whole number written as 12.0 is the count 12; anything else is left for the
    allow-list to judge."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _total(value: Any) -> int:
    """The sum of a dict of counts (what an import answered, or did not save)."""
    counts = (_whole(n) for n in _dict(value).values())
    return sum(n for n in counts if isinstance(n, int) and not isinstance(n, bool) and n > 0)


def _named(value: Any, pattern: str) -> dict[str, Any]:
    """A dict of counts as fields, each under a name built from its key. A key that gives no
    name on the allow-list is dropped there."""
    return {pattern.format(k): _whole(n) for k, n in _dict(value).items() if isinstance(k, str)}


def _pick(source: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: _whole(source.get(name)) for name in names}


def _reasons(reasons: dict[str, Any]) -> dict[str, Any]:
    """O24: the reason codes of a session or a run, as counts."""
    return {
        **_named(reasons.get("why"), "why_{}"),
        **_named(reasons.get("facts"), "fact_{}"),
    }


def _step(path: str, parent: str, base: dict[str, Any], step: dict[str, Any]) -> trace.Node | None:
    """One model request or tool call of a session (O17, level 3)."""
    start, end = _when(step.get("start")), _when(step.get("end"))
    if start is None or end is None:
        return None
    if step.get("kind") == "tool":
        tool = step.get("tool")
        name = f"tool.{tool}" if isinstance(tool, str) else ""
        return trace.Node(
            path,
            parent,
            name if trace.NAME.match(name) else "tool.other",
            "tool",
            start,
            end,
            step.get("ok") is not False,
            {**base, "tool": tool, "file": step.get("file")},
        )
    fields = {
        **base,
        "model": step.get("model") or base.get("model"),
        "tokens_in": _whole(step.get("tokens_in")),
        "tokens_out": _whole(step.get("tokens_out")),
        "tokens_cached": _whole(step.get("cache_read")),
        "tokens_cache_write": _whole(step.get("cache_write")),
    }
    name = "model.turn" if step.get("kind") == "turn" else "model.request"
    return trace.Node(path, parent, name, "llm", start, end, True, fields)


def _session(path: str, base: dict[str, Any], s: dict[str, Any], room: int) -> list[trace.Node]:
    """One harness session: its span, its export and import, and its steps, `room` at most."""
    export, back = _dict(s.get("export")), _dict(s.get("import"))
    times = [
        _when(t)
        for t in (
            export.get("start"),
            export.get("end"),
            s.get("started"),
            s.get("ended"),
            back.get("start"),
            back.get("end"),
        )
    ]
    known = [t for t in times if t is not None]
    role = s.get("role")
    if not known or role not in trace.ROLES:
        return []
    items, totals = _dict(s.get("items")), _dict(s.get("totals"))
    tokens, sizes = _dict(s.get("tokens")), _dict(s.get("sizes"))
    reasons = _dict(items.get("reasons"))
    shard, _, shards = str(s.get("shard") or "").partition("/")
    mine = {**base, "role": role, "harness": s.get("harness"), "model": s.get("model")}
    steps = [_dict(step) for step in _list(s.get("steps"))]
    inside = [
        node
        for i, step in enumerate(steps[: max(room - 3, 0)])  # room for the session and its doors
        if (node := _step(f"{path}/{i}", path, mine, step)) is not None
    ]
    doors = []
    for door, stamp in (("export", export), ("import", back)):
        start, end = _when(stamp.get("start")), _when(stamp.get("end"))
        if start is not None and end is not None:
            code = _whole(stamp.get("exit"))
            said = {**mine, "door": door, "exit_code": code}
            at = f"{path}/{door}"
            doors.append(trace.Node(at, path, f"step.{door}", "chain", start, end, code == 0, said))
    if tokens.get("estimate") is True:  # O17, level 2: from the file sizes, and said to be
        used = {"tokens_in_estimate": tokens.get("in"), "tokens_out_estimate": tokens.get("out")}
    else:
        used = {
            "total_tokens_in": tokens.get("in"),
            "total_tokens_out": tokens.get("out"),
            "total_cache_read": tokens.get("cache_read"),
            "total_cache_write": tokens.get("cache_write"),
        }
    fields = {
        **mine,
        "harness_version": s.get("harness_version"),
        "prompt_version": s.get("prompt_version"),
        "contract_version": s.get("contract_version"),
        "shard": int(shard) if shard.isdigit() else None,
        "shards": int(shards) if shards.isdigit() else None,
        "batch": s.get("batch"),
        "level": s.get("level"),
        "exit_code": s.get("exit"),
        "cause": s.get("cause"),
        "seconds": s.get("seconds"),
        "in_bytes": sizes.get("in_bytes"),
        "out_bytes": sizes.get("out_bytes"),
        "input_changed": int(bool(s.get("input_changed"))),
        "denied": s.get("denied"),
        "steps_not_sent": len(steps) - len(inside),
        **{name: _whole(n) for name, n in used.items()},
        **_pick(totals, SESSION_TOTALS),
        **_named(s.get("loaded"), "loaded_{}"),
        **_pick(items, READ if role == "reader" else WRITTEN),
        **_named(items.get("findings"), "checked_{}"),
        **_reasons(reasons),
        "reasons_dropped": reasons.get("dropped"),
    }
    if role == "reader":
        codes = _dict(items.get("not_saved"))
        fields["not_saved"] = _total(codes)
        fields.update(_named({NOT_SAVED.get(k, k): n for k, n in codes.items()}, "not_saved_{}"))
    else:
        fields["answered"] = _total(items.get("answered"))
    ok = s.get("cause") is None
    name = f"session.{role}"
    return [
        trace.Node(path, "run", name, "chain", min(known), max(known), ok, fields),
        *doors,
        *inside,
    ]


def tree(run: Any) -> tuple[str, list[trace.Node]] | None:
    """A run's trace file as the spans of one trace, and the key that names the trace; None
    when it is not a run trace."""
    run = _dict(run)
    job, started, ended = run.get("job"), _when(run.get("started")), _when(run.get("ended"))
    if run.get("v") != VERSION or job not in trace.JOBS or started is None or ended is None:
        return None
    totals = _dict(run.get("totals"))
    before, after = _dict(run.get("before")), _dict(run.get("after"))
    base = {"mode": "agent", "job": job, "harness": run.get("harness")}
    model = run.get("model")  # the models of its sessions, in one string when they differ
    fields = {
        **base,
        "checker": run.get("checker"),
        "model": "mixed" if isinstance(model, str) and "," in model else model,
        "run_stamp": run.get("stamp"),
        "shards": run.get("shards"),
        "batch_size": run.get("batch"),
        "exit_code": run.get("exit"),
        "cause": run.get("cause"),
        "seconds": int(max(ended - started, 0)),
        "total_tokens_in": _whole(totals.get("tokens_in")),
        "total_tokens_out": _whole(totals.get("tokens_out")),
        "total_cache_read": _whole(totals.get("cache_read")),
        "total_cache_write": _whole(totals.get("cache_write")),
        **_pick(totals, RUN_TOTALS),
        **_named(totals.get("causes"), "cause_{}"),
        **_reasons(totals),
        # JAP's status line before and after: what was waiting, not the rest of the line.
        **{f"{name}_before": _whole(before.get(name)) for name in trace.WAITING},
        **{f"{name}_after": _whole(after.get(name)) for name in trace.WAITING},
    }
    ok = run.get("exit") == 0
    name = f"run.{job}"
    nodes = [trace.Node("run", None, name, "chain", started, max(started, ended), ok, fields)]
    for i, s in enumerate(_list(run.get("sessions"))):
        nodes += _session(f"s{i}", base, _dict(s), MAX_SPANS - len(nodes))
        if len(nodes) >= MAX_SPANS:
            break
    stamp = run.get("stamp")
    which = stamp if isinstance(stamp, str) and trace.STAMP.match(stamp) else str(int(started))
    return f"{get_settings().jap_env}:{job}:{which}", nodes


def main() -> int:
    try:
        run = json.loads(sys.stdin.read())
    except ValueError:
        run = None
    built = tree(run)
    if built is None:
        print("not a run trace")
        return 0
    try:
        sent = trace.send_tree(*built)
    except trace.NotSent as exc:
        print(f"not sent ({exc})")
        return 1
    print("off" if sent is None else f"sent {sent} spans")
    return 0


if __name__ == "__main__":
    sys.exit(main())
