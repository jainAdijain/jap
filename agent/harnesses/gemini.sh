#!/usr/bin/env bash
# Gemini CLI on a Google plan (PRD 1.30, AH5). Contract: see common.sh.
# Non-interactive prompt, tool calls approved (the batch folder is its workspace). Its
# record of the session (O17) is its stream-json output: tool calls, and tokens at the end.
# Prove it with make agent-check HARNESS=gemini before switching (AH7).
set -u
. "$(dirname "$0")/common.sh"
need gemini
out=$(mktemp)
set -- --yolo ${JAP_MODEL:+-m "$JAP_MODEL"} -p "$(cat "$1")"
if traced; then
	gemini "$@" --output-format stream-json 2>>"$out" | record gemini "$out"
	codes=("${PIPESTATUS[@]}")
	rc=${codes[0]}
	[ "$rc" -eq 0 ] && [ "${codes[1]}" -eq 3 ] && rc=1
else
	gemini "$@" >"$out" 2>&1
	rc=$?
fi
finish "$rc" "$out"
