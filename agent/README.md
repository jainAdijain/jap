# agent/: JAP's AI work on any harness

Version 1.7, 25 Nov 2026 · follows PRD 1.37 (AH1 to AH10, O16 to O25, W8, W13) · code v0.32.0

| Version | Date | Change |
| --- | --- | --- |
| 1.7 | 25 Nov 2026 | Less work (PRD 1.37): `draft.py` skips the checker session for a draft of choices only (`JAP_CHECKER_WHEN=always` to check every draft) and adds `"_handed_out"` to the writer's import, so JAP stops handing out a job left out until its inputs change (W13). The reader is given only postings in my target roles (R3, JAP's side) |
| 1.6 | 25 Nov 2026 | Every text read whole (AH10): `wrap.py` moves each string of `in.json` over 600 characters into `text/`, wrapped at 100 characters, leaving `"@text/<file>"`; prompts reader 1.1, writer and checker 1.4. The trace adds `sizes.longest_line`, and `items.fields` and `items.unknown` for a reader |
| 1.5 | 25 Nov 2026 | Runs that finish (PRD 1.35). Every call to JAP has a time limit (`JAP_AGENT_CALL_S`, 120 s) and one that did not reach JAP is tried again (3 tries, 30 s apart); a call that never reached it is `slept` or `unreachable`, not `export_failed` or `import_failed`. The schedule tries each job every hour until 22:00 until one run has finished that day (`JAP_AGENT_SCHEDULED=1`, `data*/agent/<job>.done`), does not start in a dark wake, and keeps the Mac awake on power. JAP emails when a run broke (O25) |
| 1.4 | 24 Nov 2026 | The run's trace is handed to JAP, which sends it to the trace service; one that could not be sent waits as `<run>.unsent` and goes at the start of the next run (O22). The writer and checker say why in codes (`reasons.json`, O24; prompts 1.3): kept in the trace, a line per job in the run log, and the runner counts the jobs left out itself. A session's trace names the prompt's and the contract's version (O20) |
| 1.3 | 23 Nov 2026 | Every run leaves a trace (PRD 1.33): `trace.py`, a `trace.json` per session and a file per run, a cause for each failed session, tokens and time in the run log and in `make agent-status`. An adapter for Devin. A usage limit is read from the harness's record or its failing last words, not searched for in everything it printed |
| 1.2 | 23 Nov 2026 | The harness gets `failures.md` (a copy of `docs/failures.md`, the shared failures log) when one exists; scheduled runs keep the Mac awake (`caffeinate`) and keep `DOCKER_HOST` |
| 1.1 | 19 Nov 2026 | Writer and checker follow `review_rule` (PRD 1.29 V6): a flagged job comes back with `_review` instead of being left out |
| 1.0 | 19 Nov 2026 | First version: runner, job prompts, adapters for Claude Code, Codex, Gemini CLI and opencode, launchd schedule, contract 1 |

JAP never calls an AI harness itself. It hands out redacted batches (`make reader-export`,
`make writer-export`) and checks what comes back (`reader-import`, `writer-import`). This folder
is everything between those commands and the harness you pay for. The README's
[AI harness](../README.md#ai-harness) section is the user guide; this is the map.

```
make agent-run JOB=reader
  run.sh ── reader-status ── 8 shards in parallel, each:
  │           reader-export SHARD=i/8 LIMIT=10 > batch/in.json
  │             └─ wrap.py: long text → batch/text/*.txt, wrapped (AH10)
  │           harnesses/$JAP_HARNESS.sh jobs/reader.md   (in batch/, writes out.json)
  │             └─ trace.py read: the harness's record → batch/steps.json (numbers only)
  │           reader-import FILE=batch/out.json          (until empty or nothing saved)
  │           trace.py session → batch/trace.json, and the batch's line in the run log
  └── leftover pass (1/1), then a summary in data*/agent/last-reader.log
      and trace.py run → data*/agent/traces/<run>-reader.json
      agent-trace-send FILE=<that file>    (JAP sends it on; kept as .unsent when it cannot)

make agent-run JOB=writer
  run.sh ── writer-export > in.json, then wrap.py (text/, also given to the checker)
            harness  jobs/writer.md  → draft.json + reasons.json
            checker  jobs/checker.md → out.json + findings.txt + reasons.json   (W8; from
                     1.7 only for free text, a cover letter or a resume read: draft.py)
            writer-import FILE=out.json   (with "_handed_out": the export's ids, W13)
            the same trace files, and agent-trace-send, as the reader
```

Both jobs start by handing over any trace an earlier run could not send, once JAP has
answered its status.

## The trace (PRD 1.33, O16 to O21; 1.34, O22 and O24)

Numbers only, never text: times, token counts, counts of items, ids, and names from short
sets (harness, model, tool, a file of the batch folder, a cause, a reason code).

| Level | Who measures | What |
| --- | --- | --- |
| 1 | JAP's export and import (`backend/pipeline/agent_doors.py`, v0.28.0) | Items handed out, the export's size, items saved, and why the others were not, as codes; one `log_event` row and one span each |
| 2 | `run.sh` | Each export, session and import timed; exit; a cause when a session failed; sizes; what the import saved; a token estimate from the file sizes when the harness gave no record |
| 3 | `trace.py read`, from the harness's own record | Each model request (time, tokens in and out, cached) and each tool call (tool, file, time, ok) |

Causes: `not_logged_in`, `slept`, `timeout`, `usage_limit`, `no_output`, `export_failed`,
`import_failed`, `unreachable`. A run in which JAP did not answer has no session and the
cause `no_answer`, or `slept` or `unreachable` when its status call never reached JAP.

From 1.5 every call to JAP goes through `jap()` in `run.sh`: stopped after `JAP_AGENT_CALL_S`,
and tried again, `JAP_AGENT_CALL_TRIES` in all, when it did not reach JAP (it hit that limit,
or its error output is ssh's or Docker's). `<step>.err.why` in the batch folder then says
`slept` (the Mac's `kern.waketime` is after the call began) or `unreachable`, and that is the
session's cause. A call JAP answered with an error is not retried and stays `export_failed`
or `import_failed`.

| File | Where | Kept |
| --- | --- | --- |
| `steps.json` | Batch folder: level 3, as the adapter wrote it | 3 days |
| `trace.json` | Batch folder: the session, levels 2 and 3 together | 3 days |
| `<run>-<job>.json` | `data*/agent/traces/`: the run with all its sessions | 30 days |
| `<run>-<job>.unsent` | Beside it: JAP has not taken this trace yet | Until it is sent |

`make agent-status` shows the last run's sessions, time in the model, tokens and causes.
`JAP_AGENT_TRACE=0` stops asking a harness for its record (the adapters then run it as before
1.33); level 2 stays. Tracing needs `python3` on the Mac and never stops a run.

Which level 3 readers a real run has proven:

| Harness | Its record | Proven |
| --- | --- | --- |
| Claude Code | `--output-format stream-json --verbose` | A plan-limit reply, by a run on 23 Nov 2026. A session that worked: not yet (the limit was reached); the reader follows the tool's documents |
| Devin | `--export` (ATIF file) | Yes: `make agent-check HARNESS=devin` on 23 Nov 2026, a reader batch and a writer with its checker. The file times only when a model request began, so a request's time includes the tools it called, and a tool call has no time of its own |
| Codex, Gemini CLI, opencode | `--json`, `--output-format stream-json`, `--format json` | Not yet: not installed here |

A trace also says when a session changed its `in.json` (`input_changed`), which a harness
should only read; the checker always gets the export as JAP gave it. And it names the version
of the job prompt (the line under its title) and of the contract the session worked on.

### Why a job was left out (`reasons.json`, O24)

The writer and the checker write `reasons.json` beside their answers, keyed by application
id (or `resume:<id>`): `{"outcome": ..., "why": [...], "facts": [...]}`.

| Part | Codes |
| --- | --- |
| `outcome` | `ok`, `fixed`, `held`, `left_out` |
| `why` | `fact_not_in_bank`, `not_in_resume`, `no_option_fits`, `unclear_question`, `unsupported_claim`, `missing_answer`, `bad_option`, `too_long`, `cover_letter_rule`, `placeholder`, `ai_banned`, `other` |
| `facts` | `salary`, `notice_period`, `grades`, `travel`, `nationality`, `work_permit`, `gender`, `ethnicity`, `disability`, `other` |

`trace.py` compares each value with these lists and keeps the code, never the value: an entry
for an id that was not in the export, an outcome or a reason that is not a code, are dropped
and counted (`dropped`). The session's trace keeps them per job (`items.reasons`), the run's
trace adds them up (`totals.why`, `totals.facts`, `totals.left_out`), and the run log gets
`why (writer): <id>: left out (fact_not_in_bank; the answer bank lacks: salary)`. `left_out`
is counted by the runner from what went in and what came back, so it is there with or
without a `reasons.json`.

The lists are written in four places: `jobs/writer.md`, `jobs/checker.md`, `trace.py` and
the sender's allow-list (`backend/observability/trace.py`). `make test-agent` fails when
they differ. To add a code, add it to all four.

### Sending (O22)

`run.sh` holds no key. After a run it calls `make agent-trace-send FILE=<run trace>`, and
JAP (`backend/pipeline/agent_run_trace.py`) answers on one line:

| JAP says | The runner |
| --- | --- |
| `sent <n> spans` | Says so in the run log |
| `off` (no trace service set) | Nothing |
| `not a run trace` | Nothing: trying again would change nothing |
| `not sent (...)`, or nothing within `JAP_AGENT_SEND_S` (60 s) | Keeps `<run>-<job>.unsent` beside the file and says so in the run log |

A waiting trace is handed over at the start of the next run of either job: oldest first, 10
at most, none after the first that fails, and not one whose mark is under two minutes old
(another run is still handing it over). A mark goes when its trace file does, after 30 days.
The backend's test reads a copy of two trace files (`backend/tests/traces/agent_runs.json`);
`make test-agent` fails when `trace.py` writes another shape, and
`JAP_WRITE_FIXTURE=1 make test-agent` writes the copy again.

A record `trace.py` cannot read costs level 3 only. To record a fixture from a real session,
run it with `JAP_TRACE_KEEP=<file>`; that file holds the session's text, so cut the text out
before it goes into `tests/fixtures/` and delete the file.

## Devin's settings

`harnesses/devin.config.json` is given to Devin's tool in place of your own settings, which
may allow more (one allowed every web fetch). Tried against the tool on 23 Nov 2026:

| What | Outcome |
| --- | --- |
| A shell command | No such tool in the session |
| A web fetch | No such tool in the session |
| A write outside the batch folder | Refused |
| A read under your home folder (this checkout, its `.env` files) | Refused |
| A write to `in.json`, `prompt.md` or `failures.md` | Refused |
| Reading `in.json`, writing `out.json` | Works |

Because reads under the home folder are refused, the batch folders must be outside it, as
they are by default; the adapter stops with a message otherwise. Devin's file reader cuts
lines at 2,000 characters and a job description is one long line of `in.json`, so a session
recovers the rest by searching the file: 187 searches and 1.3 million input tokens for one
batch of 3 postings and a pair on 23 Nov. That is the harness's limit, not the trace's.

## Adding a harness

Copy `harnesses/codex.sh` to `harnesses/<name>.sh` and change the command lines. The
contract is at the top of `harnesses/common.sh`: run once in the current folder with the
prompt file given, print the harness's last words, exit 75 on a usage limit. If the harness
can print or export a record of its session, add a reader for it to `trace.py` (one function)
and a fixture; if not, leave `record` out and the runner still traces the session at level 2.
Then `make agent-check HARNESS=<name>`, and `JAP_HARNESS=<name>` in `.env.live` when it passes.

## Changing the contract

A key added to an export is not a break. Renaming or removing one, or changing a reply's
shape, is: bump `CONTRACT_VERSION` in `backend/pipeline/agent_contract.py`, the schemas in
`contract/`, and the job prompts together. `backend/tests/test_agent_contract.py` checks the
exports against the schemas; `make test-agent` runs `tests/run_test.sh` against a stand-in JAP.
