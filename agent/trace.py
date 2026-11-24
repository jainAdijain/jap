#!/usr/bin/env python3
"""The trace of JAP's AI work on a harness (PRD 1.33, O16 to O21; 1.34, O24): numbers only.

A harness session is traced at two of the PRD's three levels here (level 1, JAP's own export
and import commands, belongs to the backend):

  level 2  the runner (agent/run.sh) times each export, session and import. `session` turns
           that into the session's trace.json: start, end, exit, a cause when it failed, the
           size of what went in and came out, what the import said was saved, how many jobs
           were left out, and why, in the codes the writer or checker gave (reasons.json)
  level 3  the harness's own record of its session (a JSON event stream on its output, or
           Devin's export file). `read` keeps each model request and each tool call with its
           time and tokens, and drops everything else

    trace.py read <harness> [--record FILE --ended T] [--out steps.json] [--final FILE]
    trace.py session --dir <batch folder> --job .. --role .. --harness .. ...    run.sh
    trace.py run --run-dir <run folder> ... --out <data>/agent/traces/<run>.json run.sh
    trace.py reasons <session trace.json>     why jobs were left out, held or fixed  run.sh
    trace.py last <run trace file>                                               schedule.sh

What a trace may hold (O16): numbers, ids, and names from short sets (a harness, a model, a
tool, the files of a batch folder, a cause). Never text. A harness's record is full of text
(the prompt, job descriptions, the resume, answers), so nothing is copied from it: every
string taken from a record passes name() or file_of(), which accept a short identifier or a
file name on a fixed list and nothing else. The text a harness says at the end goes only to
--final, the batch folder's harness.log, as before 1.33. reasons.json, which a writer or
checker writes itself, is read the same way: a value is kept only when it equals a code on
OUTCOMES, WHY or FACTS, for an id that was in the export (O24).

Level 3 is proven per harness with make agent-check (AH7). A record this cannot read costs
level 3 only: nothing here may stop or fail a run, so every command swallows its own errors.
JAP_TRACE_KEEP=<file> also keeps the raw stream with arrival times, to record a test fixture
by hand; that file holds the session's text, so delete it afterwards.
Python's standard library only: it runs on the Mac beside the runner, outside Docker.
"""

import argparse
import json
import os
import re
import signal
import sys
import time
from datetime import datetime

VERSION = 1
# O18: why a session failed.
CAUSES = (
    "not_logged_in",
    "slept",
    "timeout",
    "usage_limit",
    "no_output",
    "export_failed",
    "import_failed",
)
# The files of a batch folder; any other file a tool touches is "other" or "outside".
FILES = frozenset(
    ("in.json", "out.json", "draft.json", "findings.txt", "reasons.json", "prompt.md", "failures.md")
)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,79}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Matched only against what a harness itself said when it failed, never against job text.
LIMIT = re.compile(
    r"usage limit|rate limit|limit reached|reached your .*limit|hit your .*limit|quota|"
    r"exceeded your|out of credits|too many requests",
    re.IGNORECASE,
)
LOGIN = re.compile(
    r"not logged in|please run /login|login cancell?ed|login required|auth login|"
    r"invalid api key|authentication_failed|token (has )?expired",
    re.IGNORECASE,
)
SLEPT = re.compile(r"went to sleep", re.IGNORECASE)
# Why the reader's import did not save a posting or pair (pipeline/claude_reader.py), as codes.
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
# What a writer or checker may say in reasons.json (PRD 1.34, O24): what happened to a job or
# a resume, why, and which kind of fact only the person knows the answer bank did not give.
# The job prompts (agent/jobs/writer.md, checker.md) list the same codes, and so does the
# sender's allow-list (backend/observability/trace.py); a test keeps the three in step.
OUTCOMES = ("ok", "fixed", "held", "left_out")
WHY = (
    "fact_not_in_bank",
    "not_in_resume",
    "no_option_fits",
    "unclear_question",
    "unsupported_claim",
    "missing_answer",
    "bad_option",
    "too_long",
    "cover_letter_rule",
    "placeholder",
    "ai_banned",
    "other",
)
FACTS = (
    "salary",
    "notice_period",
    "grades",
    "travel",
    "nationality",
    "work_permit",
    "gender",
    "ethnicity",
    "disability",
    "other",
)
RESUME = "resume:"
PATH_KEYS = ("file_path", "path", "absolute_path", "filePath", "notebook_path", "filename")


def name(value, default=None):
    """A harness's own short name for a model, a tool or a version; anything else is dropped."""
    text = value if isinstance(value, str) else ""
    return text if NAME.match(text) else default


def num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def file_of(path, cwd):
    """Which file of the batch folder a tool touched: its name, "other", or "outside"."""
    if not isinstance(path, str) or not path:
        return None
    full = os.path.realpath(os.path.join(cwd, path))
    if os.path.dirname(full) != os.path.realpath(cwd):
        return "outside"
    base = os.path.basename(full)
    return base if base in FILES else "other"


def path_in(args):
    if isinstance(args, dict):
        for key in PATH_KEYS:
            if isinstance(args.get(key), str):
                return args[key]
    return None


def stamp_of(value):
    """A time in a record as epoch seconds: ISO text, seconds or milliseconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value / 1000 if value > 1e11 else float(value)
    if isinstance(value, str):
        # At most six digits of a second: Devin writes nine, which older Pythons refuse.
        text = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return None
    return None


def said_why(text):
    """The cause in what a harness said when it failed, or None."""
    if LIMIT.search(text):
        return "usage_limit"
    if LOGIN.search(text):
        return "not_logged_in"
    if SLEPT.search(text):
        return "slept"
    return None


def write_json(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def tail(path, chars=2000):
    try:
        with open(path, errors="replace") as f:
            return f.read()[-chars:]
    except OSError:
        return ""


# --- level 3: a harness's own record of its session ---------------------------------------


class Record:
    """What is kept of one session's record: steps with times and tokens, and how it ended."""

    def __init__(self, harness, cwd):
        self.harness = harness
        self.cwd = cwd
        self.version = None
        self.model = None
        self.started = None
        self.ended = None
        self.mark = None  # when the harness last heard back: the next model request starts here
        self.steps = []
        self.requests = {}
        self.tools = {}
        self.events = 0
        self.unread = 0
        self.is_error = False
        self.error = None
        self.cost = None
        self.turns = None
        self.denied = 0
        self.loaded = {}
        self.final = ""  # the harness's last words: for harness.log only, never for the trace
        self.plain = []  # output lines that were not JSON: the same

    def seen(self, t):
        self.events += 1
        if self.started is None:
            self.started = self.mark = t
        self.ended = t

    def model_step(self, start, end, model=None, kind="model"):
        step = {
            "kind": kind,
            "start": end if start is None else start,
            "end": end,
            "tokens_in": 0,
            "tokens_out": 0,
            "cache_read": 0,
            "cache_write": 0,
        }
        if name(model):
            step["model"] = model
            self.model = self.model or model
        self.steps.append(step)
        return step

    def tool_start(self, key, t, tool, path=None):
        step = {"kind": "tool", "start": t, "end": t, "tool": name(tool, "other"), "ok": True}
        where = file_of(path, self.cwd)
        if where:
            step["file"] = where
        self.steps.append(step)
        if key is not None:
            self.tools[str(key)] = step
        return step

    def tool_end(self, key, t, ok=True):
        step = self.tools.pop(str(key), None)
        if step is not None:
            step["end"] = max(t, step["start"])
            step["ok"] = bool(ok)

    def fail(self, why=None, text=""):
        self.is_error = True
        self.error = self.error or (why if why in CAUSES else None) or said_why(text)

    def out(self):
        for step in self.tools.values():  # never came back: the session was cut short
            step["end"] = max(self.ended or step["start"], step["start"])
            step["ok"] = False
        steps = sorted(self.steps, key=lambda s: (s["start"], s["kind"] == "tool"))
        for step in steps:
            step["start"], step["end"] = round(step["start"], 3), round(step["end"], 3)
        asked = [s for s in steps if s["kind"] != "tool"]
        used = [s for s in steps if s["kind"] == "tool"]
        totals = {
            "model_requests": sum(s["kind"] == "model" for s in asked),
            "turns": sum(s["kind"] == "turn" for s in asked),
            "tool_calls": len(used),
            "tokens_in": sum(s["tokens_in"] for s in asked),
            "tokens_out": sum(s["tokens_out"] for s in asked),
            "cache_read": sum(s["cache_read"] for s in asked),
            "cache_write": sum(s["cache_write"] for s in asked),
            "model_ms": int(sum(s["end"] - s["start"] for s in asked) * 1000),
            "tool_ms": int(sum(s["end"] - s["start"] for s in used) * 1000),
        }
        if any(s.get("with_tools") for s in asked):
            totals["model_ms_has_tools"] = True  # the record cannot tell the two apart
        if self.cost is not None:
            totals["cost_usd"] = round(self.cost, 6)
        if self.turns:
            totals["turns"] = self.turns
        if self.is_error and not self.error:
            self.error = said_why(self.final) or "error"
        return {
            "v": VERSION,
            "level": 3,
            "harness": self.harness,
            "harness_version": self.version,
            "model": self.model,
            "started": round(self.started or 0, 3),
            "ended": round(self.ended or 0, 3),
            "steps": steps,
            "totals": totals,
            "denied": self.denied,
            "loaded": self.loaded,
            "unread_events": self.unread,
            "is_error": self.is_error,
            "error": self.error,
        }


def claude(rec, t, o):
    """Claude Code, --output-format stream-json --verbose: one JSON object per line.

    system/init names the model and what was loaded; each assistant line is one content block
    of a model reply (blocks of one request share its request_id and its usage); a user line
    carries tool results; the result line ends the session with cost, turns and any error.
    The error shape is from a real limit on 23 Nov 2026; the rest is from the tool's documents.
    """
    kind = o.get("type")
    if kind == "system" and o.get("subtype") == "init":
        rec.model = name(o.get("model"))
        rec.version = name(o.get("claude_code_version"))
        rec.loaded = {
            k: len(o[k])
            for k in ("tools", "mcp_servers", "plugins", "skills", "agents")
            if isinstance(o.get(k), list)
        }
    elif kind == "assistant":
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
        texts = [b.get("text") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        said = " ".join(x for x in texts if isinstance(x, str))
        if o.get("error") or msg.get("model") == "<synthetic>":
            # Claude Code itself speaking (a limit, a sign-in), not the model.
            why = {"rate_limit": "usage_limit", "authentication_failed": "not_logged_in"}
            rec.fail(why.get(str(o.get("error"))), said)
            rec.final = said or rec.final
        else:
            key = o.get("request_id") or msg.get("id")
            step = rec.requests.get(key) if key else None
            if step is None:
                step = rec.model_step(rec.mark, t, msg.get("model"))
                if key:
                    rec.requests[key] = step
            step["end"] = t
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            read, wrote = num(usage.get("cache_read_input_tokens")), num(
                usage.get("cache_creation_input_tokens")
            )
            step["tokens_in"] = num(usage.get("input_tokens")) + read + wrote
            step["tokens_out"] = num(usage.get("output_tokens"))
            step["cache_read"], step["cache_write"] = read, wrote
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    rec.tool_start(b.get("id"), t, b.get("name"), path_in(b.get("input")))
            rec.final = said or rec.final
        rec.mark = t
    elif kind == "user":
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                rec.tool_end(b.get("tool_use_id"), t, not b.get("is_error"))
        rec.mark = t
    elif kind == "result":
        if isinstance(o.get("result"), str):
            rec.final = o["result"]
        if "total_cost_usd" in o:
            rec.cost = num(o.get("total_cost_usd"))
        rec.turns = int(num(o.get("num_turns"))) or None
        denials = o.get("permission_denials")
        rec.denied = len(denials) if isinstance(denials, list) else 0
        if o.get("is_error"):
            api = f"{o.get('api_error')} {o.get('api_error_type')}"
            limit = "limit" in api or o.get("api_error_status") == 429
            rec.fail("usage_limit" if limit else None, rec.final)


def codex(rec, t, o):
    """OpenAI Codex, exec --json (from its documents; not yet proven by a run): thread and
    turn events, items started and completed, and token usage once per turn, not per request."""
    kind = o.get("type")
    item = o.get("item") if isinstance(o.get("item"), dict) else {}
    tools = {"command_execution": "shell", "file_change": "file_change", "web_search": "web_search"}
    if kind == "turn.started":
        rec.mark = t
    elif kind in ("item.started", "item.completed"):
        what = item.get("type")
        if what == "mcp_tool_call":
            tool = f"mcp__{item.get('server')}__{item.get('tool')}"
        else:
            tool = tools.get(what)
        key = item.get("id")
        if tool and kind == "item.started":
            rec.tool_start(key, t, tool)
        elif tool:
            if str(key) not in rec.tools:
                rec.tool_start(key, t, tool)
            rec.tool_end(key, t, item.get("status") not in ("failed", "declined"))
        elif what == "agent_message" and isinstance(item.get("text"), str):
            rec.final = item["text"]
    elif kind == "turn.completed":
        usage = o.get("usage") if isinstance(o.get("usage"), dict) else {}
        step = rec.model_step(rec.mark, t, kind="turn")
        step["tokens_in"] = num(usage.get("input_tokens"))  # cached input is part of it
        step["tokens_out"] = num(usage.get("output_tokens"))
        step["cache_read"] = num(usage.get("cached_input_tokens"))
        rec.mark = t
    elif kind == "turn.failed":
        error = o.get("error") if isinstance(o.get("error"), dict) else {}
        rec.final = str(error.get("message") or "")
        rec.fail(text=rec.final)
    elif kind == "error":
        text = str(o.get("message") or "")
        if not text.startswith("Reconnecting"):
            rec.final = text


def gemini(rec, t, o):
    """Gemini CLI, --output-format stream-json (from its documents; not yet proven by a run):
    init, message, tool_use, tool_result, error, and a result with the session's totals."""
    kind = o.get("type")
    if kind == "init":
        rec.model = name(o.get("model"))
    elif kind == "message":
        if o.get("role") == "assistant" and isinstance(o.get("content"), str):
            rec.final = (rec.final + o["content"]) if o.get("delta") else o["content"]
    elif kind == "tool_use":
        key = o.get("tool_id") or o.get("id")
        rec.tool_start(key, t, o.get("tool_name") or o.get("name"), path_in(o.get("parameters")))
    elif kind == "tool_result":
        rec.tool_end(o.get("tool_id") or o.get("id"), t, o.get("status") in (None, "success"))
    elif kind == "error":
        rec.final = str(o.get("message") or rec.final)
    elif kind == "result":
        stats = o.get("stats") if isinstance(o.get("stats"), dict) else {}
        step = rec.model_step(rec.started, t, kind="turn")
        step["tokens_in"] = num(stats.get("input_tokens"))
        step["tokens_out"] = num(stats.get("output_tokens"))
        step["cache_read"] = num(stats.get("cached"))
        if o.get("status") not in (None, "success"):
            rec.fail(text=f"{o.get('error')} {rec.final}")


def opencode(rec, t, o):
    """opencode, run --format json (from its documents; not yet proven by a run): step_start
    and step_finish around each model request, tool_use when a tool has run, text, error."""
    kind = o.get("type")
    part = o.get("part") if isinstance(o.get("part"), dict) else {}
    if kind == "step_start":
        rec.mark = t
    elif kind == "step_finish":
        tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        step = rec.model_step(rec.mark, t)
        read, wrote = num(cache.get("read")), num(cache.get("write"))
        step["tokens_in"] = num(tokens.get("input")) + read + wrote
        step["tokens_out"] = num(tokens.get("output")) + num(tokens.get("reasoning"))
        step["cache_read"], step["cache_write"] = read, wrote
        if "cost" in part:
            rec.cost = (rec.cost or 0) + num(part.get("cost"))
        rec.mark = t
    elif kind == "tool_use":
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        took = state.get("time") if isinstance(state.get("time"), dict) else {}
        start, end = stamp_of(took.get("start")) or t, stamp_of(took.get("end")) or t
        key = part.get("callID") or part.get("id") or len(rec.steps)
        rec.tool_start(key, start, part.get("tool"), path_in(state.get("input")))
        rec.tool_end(key, end, state.get("status") != "error")
        rec.mark = t
    elif kind == "text" and isinstance(part.get("text"), str):
        rec.final = part["text"]
    elif kind == "error":
        rec.fail(text=json.dumps(o.get("error"))[:500])


def devin(rec, data, ended=None):
    """Devin's command line tool, --export: one ATIF file (Agent Trajectory Interchange
    Format, v1.7 on 23 Nov 2026, from real sessions that day). A step per message; an agent
    step is one model request, with the time it began, its tokens, the tools it called and
    what each answered. The file times only the start of a request, so a request's time here
    runs to the start of the next one and includes the tools it called (with_tools), and a
    tool call has no time of its own (timed: false). The last request ends when the tool
    exited (ended, from the adapter), else its length is unknown and counted as nothing.
    """
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    rec.version = name(agent.get("version"))
    tools = agent.get("tool_definitions")
    if isinstance(tools, list):
        rec.loaded = {"tools": len(tools)}
    cost, last = 0.0, None
    steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    for raw in steps:
        t = stamp_of(raw.get("timestamp")) if isinstance(raw, dict) else None
        if t is None:
            continue
        rec.seen(t)
        if raw.get("source") != "agent":
            continue
        if last is not None:
            last["end"] = max(t, last["start"])
        metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
        last = rec.model_step(t, t, raw.get("model_name"))
        last["tokens_in"] = num(metrics.get("prompt_tokens"))  # cached tokens are part of it
        last["tokens_out"] = num(metrics.get("completion_tokens"))
        last["cache_read"] = num(metrics.get("cached_tokens"))
        cost += num(metrics.get("cost_usd"))
        seen = raw.get("observation") if isinstance(raw.get("observation"), dict) else {}
        results = seen.get("results") if isinstance(seen.get("results"), list) else []
        answers = {r.get("source_call_id"): r.get("content") for r in results if isinstance(r, dict)}
        calls = raw.get("tool_calls") if isinstance(raw.get("tool_calls"), list) else []
        for call in (c for c in calls if isinstance(c, dict)):
            step = rec.tool_start(None, t, call.get("function_name"), path_in(call.get("arguments")))
            step["timed"] = False
            last["with_tools"] = True
            # Only how the answer begins is looked at: refused (nobody to approve it) or failed.
            said = answers.get(call.get("tool_call_id"))
            said = said[:60] if isinstance(said, str) else ""
            if said.startswith("Tool execution was rejected"):
                step["ok"] = False
                rec.denied += 1
            elif "validation failed" in said or said.startswith("Error"):
                step["ok"] = False
    if last is not None and isinstance(ended, (int, float)) and ended >= last["start"]:
        last["end"] = rec.ended = float(ended)
    rec.model = rec.model or name(agent.get("model_name"))
    final = data.get("final_metrics") if isinstance(data.get("final_metrics"), dict) else {}
    total = num(final.get("total_cost_usd")) or cost
    rec.cost = total if total else None


STREAMS = {"claude": claude, "fake": claude, "codex": codex, "gemini": gemini, "opencode": opencode}
FILES_OF = {"devin": devin}


def cmd_read(a):
    rec = Record(a.harness, os.getcwd())
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # the runner's timeout: keep what there is
    keep = None
    try:
        if a.record:
            data = load(a.record)
            if isinstance(data, dict) and a.harness in FILES_OF:
                ended = float(a.ended) if re.fullmatch(r"\d+(\.\d+)?", a.ended) else None
                FILES_OF[a.harness](rec, data, ended)
        else:
            reader = STREAMS.get(a.harness)
            if os.environ.get("JAP_TRACE_KEEP"):
                keep = open(os.environ["JAP_TRACE_KEEP"], "a")
            for line in iter(sys.stdin.readline, ""):
                t = time.time()
                try:
                    o = json.loads(line)
                except ValueError:
                    o = None
                if not isinstance(o, dict):
                    if line.strip():
                        rec.plain.append(line.rstrip("\n"))
                    continue
                if isinstance(o.get("_t"), (int, float)):
                    t = o["_t"]  # a kept stream replayed: its own arrival times
                rec.seen(t)
                if keep:
                    keep.write(json.dumps({**o, "_t": round(t, 3)}) + "\n")
                try:
                    if reader:
                        reader(rec, t, o)
                except Exception:
                    rec.unread += 1
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if keep:
            keep.close()
        if rec.events and (a.record or a.harness in STREAMS):
            try:
                write_json(a.out, rec.out())
            except OSError:
                pass
        if a.final:
            said = [rec.final.strip()] if rec.final.strip() else []
            try:
                with open(a.final, "a") as f:
                    f.write("".join(f"{line}\n" for line in said + rec.plain))
            except OSError:
                pass
    return 3 if rec.is_error else 0


# --- level 2: one session as the runner saw it --------------------------------------------


def stamp(folder, step):
    """A step's "<start> <end> <exit code>" as run.sh wrote it, or None when it never ran."""
    try:
        with open(os.path.join(folder, f"t.{step}")) as f:
            start, end, rc = (int(x) for x in f.read().split()[:3])
    except (OSError, ValueError):
        return None
    return {"start": start, "end": max(end, start), "exit": rc}


def ids(rows, key):
    found = [r.get(key) for r in rows if isinstance(r, dict)]
    return [x for x in found if isinstance(x, str) and UUID.match(x)]


def rows(data, key):
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, list) else []


def reader_items(folder):
    given, got = load(os.path.join(folder, "in.json")), load(os.path.join(folder, "out.json"))
    items = {
        "postings": len(rows(given, "postings")),
        "pairs": len(rows(given, "pairs")),
        "written": len(got) if isinstance(got, dict) else 0,
    }
    saved = {"saved": 0, "merged": 0, "kept_apart": 0}
    not_saved = {}
    for line in tail(os.path.join(folder, "import.txt"), 200_000).splitlines():
        _, sep, said = line.partition(": ")
        if not sep:
            continue
        if said.startswith("not saved"):
            code = next((c for words, c in NOT_SAVED if words in said), "invalid_value")
            not_saved[code] = not_saved.get(code, 0) + 1
        elif said.replace(" ", "_") in saved:
            saved[said.replace(" ", "_")] += 1
    items.update(saved)
    items["not_saved"] = not_saved
    items["posting_ids"] = ids(rows(given, "postings"), "posting_id")
    return items


def listed(value):
    """A value of reasons.json as a list: one code, or a list of them."""
    if isinstance(value, str):
        return [value]
    return value if isinstance(value, list) else []


def on_list(values, allowed):
    """The values that are codes on that list, each once, in the list's order."""
    return [code for code in allowed if any(isinstance(v, str) and v == code for v in values)]


def reasons_of(folder, known):
    """O24: reasons.json as the writer or checker wrote it, keeping only what is on the lists.

    known: the ids of the export (application ids, and "resume:<id>"). An entry for any other
    key, or with an outcome that is not on OUTCOMES, is dropped; so is each reason or kind of
    fact that is not a code on WHY or FACTS. Values are compared with the codes, never copied,
    so whatever else a harness wrote there (a sentence, a company, a question) stays out.
    """
    data = load(os.path.join(folder, "reasons.json"))
    out = {
        "given": isinstance(data, dict),
        "outcomes": {},
        "why": {},
        "facts": {},
        "dropped": 0,
        "items": [],
    }
    for key, said in data.items() if isinstance(data, dict) else ():
        said = said if isinstance(said, dict) else {}
        outcome = next((o for o in OUTCOMES if said.get("outcome") == o), None)
        if key not in known or outcome is None:
            out["dropped"] += 1
            continue
        why, facts = listed(said.get("why")), listed(said.get("facts"))
        kept = {"why": on_list(why, WHY), "facts": on_list(facts, FACTS)}
        out["dropped"] += sum(not (isinstance(v, str) and v in WHY) for v in why)
        out["dropped"] += sum(not (isinstance(v, str) and v in FACTS) for v in facts)
        out["outcomes"][outcome] = out["outcomes"].get(outcome, 0) + 1
        for part in ("why", "facts"):
            for code in kept[part]:
                out[part][code] = out[part].get(code, 0) + 1
        out["items"].append({"id": key, "outcome": outcome, **kept})
    return out


def missing(given, wrote):
    """How many of the jobs handed out have no entry in what came back."""
    return sum(1 for key in given if key not in wrote)


def writer_items(folder, role):
    given = load(os.path.join(folder, "in.json"))
    wrote = load(os.path.join(folder, "out.json" if role == "checker" else "draft.json"))
    wrote = wrote if isinstance(wrote, dict) else {}
    jobs = {k: v for k, v in wrote.items() if not k.startswith(RESUME)}
    if role == "checker":  # the checker is handed the writer's draft: what did it not pass on
        drafted = load(os.path.join(folder, "draft.json"))
        drafted = drafted if isinstance(drafted, dict) else {}
        handed = [k for k in drafted if not k.startswith(RESUME)]
    else:
        handed = [j.get("application_id") for j in rows(given, "jobs") if isinstance(j, dict)]
    known = set(ids(rows(given, "jobs"), "application_id"))
    known |= {RESUME + r for r in ids(rows(given, "resumes"), "resume_id")}
    items = {
        "jobs": len(rows(given, "jobs")),
        "questions": sum(len(rows(j, "questions")) for j in rows(given, "jobs")),
        "resumes": len(rows(given, "resumes")),
        "written": len(jobs),
        "held": sum(isinstance(v, dict) and "_review" in v for v in jobs.values()),
        "resumes_written": len(wrote) - len(jobs),
        # Counted here, not taken from the harness: what went in and did not come back.
        "left_out": missing(handed, jobs),
        "reasons": reasons_of(folder, known),
        "application_ids": ids(rows(given, "jobs"), "application_id"),
    }
    if role == "checker":
        found = {"ok": 0, "fixed": 0, "held": 0, "left_out": 0}
        for line in tail(os.path.join(folder, "findings.txt"), 200_000).splitlines():
            said = line.partition(": ")[2].strip().lower()
            for words, key in (("ok", "ok"), ("fixed", "fixed"), ("held", "held"), ("left out", "left_out")):
                if said.startswith(words):
                    found[key] += 1
        items["findings"] = found
    else:
        answered, refused, read = {}, 0, 0
        for line in tail(os.path.join(folder, "import.txt"), 200_000).splitlines():
            key, sep, said = line.partition(": ")
            if not sep:
                continue
            if key.startswith("resume:"):
                read += said.startswith("read")
            elif said.startswith("not answered"):
                refused += 1
            elif re.fullmatch(r"[a-z_]{2,40}", said):
                answered[said] = answered.get(said, 0) + 1
        items.update({"answered": answered, "not_answered": refused, "resumes_read": read})
    return items


def why_failed(folder, a, spans, steps):
    """O18: the one cause of a failed session, or None when it did its work."""
    ran, sent, back = spans["session"], spans["export"], spans["import"]
    if sent and sent["exit"] != 0:
        return "export_failed"
    if os.path.exists(os.path.join(folder, "timed-out")):
        return "timeout"
    said = (steps or {}).get("error")
    if (ran and ran["exit"] == 75) or said == "usage_limit":
        return "usage_limit"
    if ran and not size(os.path.join(folder, a.expects)):
        if said in CAUSES:
            return said
        if ran["exit"] != 0:  # the harness failed, so its last words are its own
            return said_why(tail(os.path.join(folder, "harness.log"))) or "no_output"
        return "no_output"
    if back and back["exit"] != 0:
        return "import_failed"
    return None


def count(n):
    return f"{int(n):,}"


def plural(n, word):
    return f"{count(n)} {word}{'' if n == 1 else 's'}"


def took(seconds):
    return f"{seconds / 60:.0f} min" if seconds >= 90 else f"{seconds:.0f} s"


def line_of(trace):
    """What the run log says about one session after its outcome."""
    parts = [took(trace["seconds"])]
    tokens, totals = trace["tokens"], trace.get("totals") or {}
    if trace["level"] == 3:
        if totals.get("model_requests"):
            parts.append(plural(totals["model_requests"], "model request"))
        elif totals.get("turns"):
            parts.append(plural(totals["turns"], "turn"))
        if totals.get("tool_calls"):
            parts.append(plural(totals["tool_calls"], "tool call"))
        parts.append(f"{count(tokens['in'])} tokens in, {count(tokens['out'])} out")
    else:
        parts.append(
            f"files of about {count(tokens['in'])} tokens in, {count(tokens['out'])} out "
            "(an estimate: the harness gave no record)"
        )
    parts.append(trace["model"])
    if trace.get("input_changed"):
        parts.append("it changed its in.json")
    return " · " + " · ".join(parts)


def versions(folder):
    """O20: the version the job prompt carries under its title ("... · version 1.3, 24 Nov
    2026") and the contract version of the export, or None where one is not there."""
    head = ""
    try:
        with open(os.path.join(folder, "prompt.md"), encoding="utf-8", errors="replace") as f:
            head = f.read(600)
    except OSError:
        pass
    said = re.search(r"· version (\d+(?:\.\d+){0,2}),", head)
    given = load(os.path.join(folder, "in.json"))
    contract = given.get("contract_version") if isinstance(given, dict) else None
    ok = isinstance(contract, int) and not isinstance(contract, bool) and 0 <= contract < 1000
    return (said.group(1) if said else None), (contract if ok else None)


def cmd_session(a):
    folder = a.dir
    prompt, contract = versions(folder)
    spans = {step: stamp(folder, step) for step in ("export", "session", "import")}
    steps = load(os.path.join(folder, "steps.json"))
    steps = steps if isinstance(steps, dict) and steps.get("level") == 3 else None
    ran = spans["session"]
    fed = ["prompt.md", "in.json", "failures.md"] + (["draft.json"] if a.role == "checker" else [])
    made = [a.expects] + (["findings.txt"] if a.role == "checker" else [])
    sizes = {
        "in_bytes": sum(size(os.path.join(folder, f)) for f in fed),
        "out_bytes": sum(size(os.path.join(folder, f)) for f in made),
    }
    totals = (steps or {}).get("totals") or {}
    if steps:
        tokens = {k: int(num(totals.get(f))) for k, f in (
            ("in", "tokens_in"), ("out", "tokens_out"),
            ("cache_read", "cache_read"), ("cache_write", "cache_write"))}
        tokens["estimate"] = False
    else:  # O17, level 2: one pass over the files at about 4 characters a token
        tokens = {"in": sizes["in_bytes"] // 4, "out": sizes["out_bytes"] // 4, "estimate": True}
    trace = {
        "v": VERSION,
        "job": a.job,
        "role": a.role,
        "harness": name(a.harness, "other"),
        "harness_version": (steps or {}).get("harness_version"),
        "model": name((steps or {}).get("model")) or name(a.model) or "default",
        "prompt_version": prompt,
        "contract_version": contract,
        "shard": a.shard if re.fullmatch(r"\d+/\d+", a.shard or "") else None,
        "batch": int(a.batch) if (a.batch or "").isdigit() else None,
        "level": 3 if steps else 2,
        "started": ran["start"] if ran else None,
        "ended": ran["end"] if ran else None,
        "seconds": (ran["end"] - ran["start"]) if ran else 0,
        "exit": ran["exit"] if ran else None,
        "cause": why_failed(folder, a, spans, steps),
        "export": spans["export"],
        "import": spans["import"],
        "sizes": sizes,
        "tokens": tokens,
        # run.sh found in.json different after the session: a harness should only read it.
        "input_changed": os.path.exists(os.path.join(folder, "input-changed")),
        "items": reader_items(folder) if a.role == "reader" else writer_items(folder, a.role),
        "steps": (steps or {}).get("steps") or [],
        "totals": totals,
        "denied": (steps or {}).get("denied", 0),
        "loaded": (steps or {}).get("loaded") or {},
    }
    write_json(os.path.join(folder, "trace.json"), trace)
    if a.say == "cause":
        print(trace["cause"] or "")
    elif a.say == "line" and ran:
        print(line_of(trace))
    return 0


# --- one run ------------------------------------------------------------------------------


def status(path):
    """A status line of JAP's (unread=12 pairs=0, jobs=1 resumes=0) as numbers."""
    return {k: int(v) for k, v in re.findall(r"\b([a-z_]{2,20})=(\d+)\b", tail(path, 500))}


def summary(run):
    t = run["totals"]
    if not t["sessions"]:
        return "no session ran" + (f" ({run['cause']})" if run.get("cause") else "")
    parts = [plural(t["sessions"], "session")]
    if t["failed"]:
        why = ", ".join(f"{k} {v}" for k, v in sorted(t["causes"].items()))
        parts[0] += f", {t['failed']} failed ({why})"
    if t["traced"]:
        both = " and its tools" if t.get("model_ms_has_tools") else ""
        parts.append(f"{took(t['model_ms'] / 1000)} in the model{both}")
        parts.append(f"{count(t['tokens_in'])} tokens in, {count(t['tokens_out'])} out")
        if t["traced"] < t["sessions"]:
            parts.append(f"{plural(t['sessions'] - t['traced'], 'session')} gave no record")
    else:
        parts.append(f"{took(t['seconds'])} in sessions, no record from the harness")
    if t.get("left_out"):  # O24: by the writer or the checker; the run log says why
        parts.append(f"{plural(t['left_out'], 'job')} left out")
    parts.append(run["model"])
    return " · ".join(parts)


def added(sessions, part):
    """The reason codes of every session of a run, counted together (part: why or facts)."""
    out = {}
    for s in sessions:
        said = (s["items"].get("reasons") or {}).get(part) or {}
        for code, n in said.items():
            out[code] = out.get(code, 0) + int(num(n))
    return out


def cmd_run(a):
    sessions = []
    for folder, _, files in os.walk(a.run_dir):
        if "trace.json" in files:
            one = load(os.path.join(folder, "trace.json"))
            if isinstance(one, dict) and one.get("v") == VERSION:
                sessions.append(one)
    sessions.sort(key=lambda s: (s.get("started") or 0, s.get("shard") or "", s.get("batch") or 0))
    traced = [s for s in sessions if s["level"] == 3]
    causes = {}
    for s in sessions:
        if s.get("cause"):
            causes[s["cause"]] = causes.get(s["cause"], 0) + 1
    models = sorted({s["model"] for s in traced if s["model"] != "default"})
    totals = {
        "sessions": len(sessions),
        "traced": len(traced),
        "failed": sum(causes.values()),
        "causes": causes,
        "seconds": sum(s["seconds"] for s in sessions),
        "model_requests": sum(num(s["totals"].get("model_requests")) for s in traced),
        "tool_calls": sum(num(s["totals"].get("tool_calls")) for s in traced),
        "tokens_in": sum(s["tokens"]["in"] for s in traced),
        "tokens_out": sum(s["tokens"]["out"] for s in traced),
        "cache_read": sum(s["tokens"].get("cache_read", 0) for s in traced),
        "cache_write": sum(s["tokens"].get("cache_write", 0) for s in traced),
        "model_ms": sum(num(s["totals"].get("model_ms")) for s in traced),
        "tool_ms": sum(num(s["totals"].get("tool_ms")) for s in traced),
        "model_ms_has_tools": any(s["totals"].get("model_ms_has_tools") for s in traced),
        "input_changed": sum(bool(s.get("input_changed")) for s in sessions),
        "cost_usd": round(sum(num(s["totals"].get("cost_usd")) for s in traced), 6),
        "denied": sum(num(s.get("denied")) for s in sessions),
        "saved": sum(num(s["items"].get("saved")) for s in sessions if s["role"] == "reader"),
        "pairs_settled": sum(
            num(s["items"].get("merged")) + num(s["items"].get("kept_apart"))
            for s in sessions
            if s["role"] == "reader"
        ),
        "left_out": sum(int(num(s["items"].get("left_out"))) for s in sessions),
        "why": added(sessions, "why"),
        "facts": added(sessions, "facts"),
        "reasons_dropped": sum(
            int(num((s["items"].get("reasons") or {}).get("dropped"))) for s in sessions
        ),
    }
    cause = tail(os.path.join(a.run_dir, "cause"), 40).strip()
    run = {
        "v": VERSION,
        "job": a.job,
        "env": name(a.env, "other"),
        "stamp": name(a.stamp),
        "harness": name(a.harness, "other"),
        "checker": name(a.checker),
        "model": ", ".join(models) or name(a.model) or "default",
        "shards": int(a.shards) if a.shards.isdigit() else None,
        "batch": int(a.batch) if a.batch.isdigit() else None,
        "started": int(a.start),
        "ended": int(time.time()),
        "exit": int(a.rc),
        "cause": cause if re.fullmatch(r"[a-z_]{2,30}", cause) else None,
        "before": status(os.path.join(a.run_dir, "before.txt")),
        "after": status(os.path.join(a.run_dir, "after.txt")),
        "totals": totals,
        "sessions": sessions,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    write_json(a.out, run)
    if sessions:
        print(f"Trace: {summary(run)} · kept in {a.out}")
    return 0


def cmd_reasons(a):
    """O24, for the run log: one line per job or resume that was left out, held or fixed,
    in the codes kept in the session's trace, and how many were left out with none given."""
    trace = load(a.file)
    items = trace.get("items") if isinstance(trace, dict) and trace.get("v") == VERSION else None
    if not isinstance(items, dict):
        return 0
    explained = 0
    for one in (items.get("reasons") or {}).get("items") or []:
        if one["outcome"] == "ok":
            continue
        explained += one["outcome"] == "left_out"
        detail = ", ".join(one["why"]) or "no reason given"
        if one["facts"]:
            detail += f"; the answer bank lacks: {', '.join(one['facts'])}"
        print(f"{one['id']}: {one['outcome'].replace('_', ' ')} ({detail})")
    unexplained = int(num(items.get("left_out"))) - explained
    if unexplained > 0:
        print(f"{plural(unexplained, 'job')} left out with no reason given")
    return 0


def cmd_last(a):
    run = load(a.file)
    if isinstance(run, dict) and run.get("v") == VERSION:
        when = datetime.fromtimestamp(run["started"]).strftime("%d %b %H:%M")
        print(f"{when}: {summary(run)}")
    return 0


def main(argv):
    p = argparse.ArgumentParser(prog="trace.py")
    sub = p.add_subparsers(dest="command", required=True)
    read = sub.add_parser("read")
    read.add_argument("harness")
    read.add_argument("--record", default="")
    read.add_argument("--ended", default="")
    read.add_argument("--out", default="steps.json")
    read.add_argument("--final", default="")
    session = sub.add_parser("session")
    for flag in ("--dir", "--job", "--role", "--harness", "--expects"):
        session.add_argument(flag, required=True)
    for flag in ("--model", "--shard", "--batch"):
        session.add_argument(flag, default="")
    session.add_argument("--say", default="line", choices=("line", "cause", "none"))
    run = sub.add_parser("run")
    for flag in ("--run-dir", "--job", "--env", "--harness", "--start", "--rc", "--out"):
        run.add_argument(flag, required=True)
    for flag in ("--model", "--checker", "--stamp", "--shards", "--batch"):
        run.add_argument(flag, default="")
    for command in ("reasons", "last"):
        sub.add_parser(command).add_argument("file")
    a = p.parse_args(argv)
    commands = {
        "read": cmd_read,
        "session": cmd_session,
        "run": cmd_run,
        "reasons": cmd_reasons,
        "last": cmd_last,
    }
    try:
        return commands[a.command](a)
    except Exception as exc:  # a trace is never worth a run
        print(f"trace.py {a.command}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
