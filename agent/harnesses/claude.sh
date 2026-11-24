#!/usr/bin/env bash
# Claude Code on a Claude plan (PRD 1.30, AH5). Contract: see common.sh.
# Print mode, file edits in the batch folder allowed, no shell and no web. Its record of the
# session (O17) is its stream-json output: one line per model reply and tool result.
set -u
. "$(dirname "$0")/common.sh"
need claude
out=$(mktemp)
set -- -p "$(cat "$1")" --permission-mode acceptEdits \
	--disallowedTools "Bash,WebFetch,WebSearch,Task,Agent" \
	${JAP_MODEL:+--model "$JAP_MODEL"}
if traced; then
	claude "$@" --output-format stream-json --verbose 2>>"$out" | record claude "$out"
	codes=("${PIPESTATUS[@]}")
	rc=${codes[0]}
	[ "$rc" -eq 0 ] && [ "${codes[1]}" -eq 3 ] && rc=1
else
	claude "$@" --output-format text >"$out" 2>&1
	rc=$?
fi
finish "$rc" "$out"
