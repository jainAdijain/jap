#!/usr/bin/env bash
# opencode with any provider it is signed in to (PRD 1.30, AH5). Contract: see common.sh.
# JAP_MODEL is provider/model, for example anthropic/claude-sonnet-4-5. Its record of the
# session (O17) is its --format json output: a line per model step and tool call.
# Prove it with make agent-check HARNESS=opencode before switching (AH7).
set -u
. "$(dirname "$0")/common.sh"
need opencode
out=$(mktemp)
set -- run ${JAP_MODEL:+-m "$JAP_MODEL"}
if traced; then
	opencode "$@" --format json "$(cat "$1")" 2>>"$out" | record opencode "$out"
	codes=("${PIPESTATUS[@]}")
	rc=${codes[0]}
	[ "$rc" -eq 0 ] && [ "${codes[1]}" -eq 3 ] && rc=1
else
	opencode "$@" "$(cat "$1")" >"$out" 2>&1
	rc=$?
fi
finish "$rc" "$out"
