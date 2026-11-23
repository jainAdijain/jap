# agent/: JAP's AI work on any harness

Version 1.2, 23 Nov 2026 · follows PRD 1.30 (AH1 to AH8) · code v0.26.2

| Version | Date | Change |
| --- | --- | --- |
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
  │           reader-import FILE=batch/out.json          (until empty or nothing saved)
  └── leftover pass (1/1), then a summary in data*/agent/last-reader.log

make agent-run JOB=writer
  run.sh ── writer-export > in.json
            harness  jobs/writer.md  → draft.json
            checker  jobs/checker.md → out.json + findings.txt   (W8)
            writer-import FILE=out.json
```

## Adding a harness

Copy `harnesses/codex.sh` to `harnesses/<name>.sh` and change the one command line. The
contract is at the top of `harnesses/common.sh`: run once in the current folder with the
prompt file given, print the output, exit 75 on a usage limit. Then
`make agent-check HARNESS=<name>`, and `JAP_HARNESS=<name>` in `.env.live` when it passes.

## Changing the contract

A key added to an export is not a break. Renaming or removing one, or changing a reply's
shape, is: bump `CONTRACT_VERSION` in `backend/pipeline/agent_contract.py`, the schemas in
`contract/`, and the job prompts together. `backend/tests/test_agent_contract.py` checks the
exports against the schemas; `make test-agent` runs `tests/run_test.sh` against a stand-in JAP.
