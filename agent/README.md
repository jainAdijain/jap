# agent/: JAP's AI work on any harness

Version 1.3, 23 Nov 2026 · follows PRD 1.33 (AH1 to AH9, O16 to O21) · code v0.27.0

| Version | Date | Change |
| --- | --- | --- |
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
  │           harnesses/$JAP_HARNESS.sh jobs/reader.md   (in batch/, writes out.json)
  │             └─ trace.py read: the harness's record → batch/steps.json (numbers only)
  │           reader-import FILE=batch/out.json          (until empty or nothing saved)
  │           trace.py session → batch/trace.json, and the batch's line in the run log
  └── leftover pass (1/1), then a summary in data*/agent/last-reader.log
      and trace.py run → data*/agent/traces/<run>-reader.json

make agent-run JOB=writer
  run.sh ── writer-export > in.json
            harness  jobs/writer.md  → draft.json
            checker  jobs/checker.md → out.json + findings.txt   (W8)
            writer-import FILE=out.json
```

## The trace (PRD 1.33, O16 to O21)

Numbers only, never text: times, token counts, counts of items, ids, and names from short
sets (harness, model, tool, a file of the batch folder, a cause).

| Level | Who measures | What |
| --- | --- | --- |
| 1 | JAP's export and import (`backend/pipeline/agent_doors.py`, v0.28.0) | Items handed out, the export's size, items saved, and why the others were not, as codes; one `log_event` row and one span each |
| 2 | `run.sh` | Each export, session and import timed; exit; a cause when a session failed; sizes; what the import saved; a token estimate from the file sizes when the harness gave no record |
| 3 | `trace.py read`, from the harness's own record | Each model request (time, tokens in and out, cached) and each tool call (tool, file, time, ok) |

Causes: `not_logged_in`, `slept`, `timeout`, `usage_limit`, `no_output`, `export_failed`,
`import_failed`. A run in which JAP did not answer has no session and the cause `no_answer`.

| File | Where | Kept |
| --- | --- | --- |
| `steps.json` | Batch folder: level 3, as the adapter wrote it | 3 days |
| `trace.json` | Batch folder: the session, levels 2 and 3 together | 3 days |
| `<run>-<job>.json` | `data*/agent/traces/`: the run with all its sessions | 30 days |

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
should only read; the checker always gets the export as JAP gave it.

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
