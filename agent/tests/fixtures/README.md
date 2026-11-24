# Records of harness sessions, for agent/tests/run_test.sh

Every text in these files is a stand-in: a real record holds the prompt, job text and
answers, and none of that belongs in git.

| File | Where it is from |
| --- | --- |
| `devin-atif.json` | A real session of Devin's command line tool under JAP's settings, 23 Nov 2026 (ATIF v1.7): times, tokens, tool names and which call was refused are as recorded; texts replaced, ids shortened |
| `claude-limit.jsonl` | The shape of Claude Code's real reply at its plan limit, 23 Nov 2026; ids left out, the reset time changed |
| `claude-ok.jsonl` | Built in Claude Code's documented stream shape, with the times and token counts of a real reader session (22 Nov 2026). Not recorded from a run |
| `codex.jsonl`, `gemini.jsonl`, `opencode.jsonl` | Built from each tool's documents. Not recorded from a run |

`_t` on a line is when it arrived, in seconds; `trace.py read` uses it in place of the clock.
