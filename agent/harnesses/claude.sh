#!/usr/bin/env bash
# Claude Code on a Claude plan (PRD 1.30, AH5). Contract: see common.sh.
# Print mode, file edits in the batch folder allowed, no shell and no web.
set -u
. "$(dirname "$0")/common.sh"
need claude
out=$(mktemp)
set -- -p "$(cat "$1")" --permission-mode acceptEdits \
	--disallowedTools "Bash,WebFetch,WebSearch,Task,Agent" --output-format text \
	${JAP_MODEL:+--model "$JAP_MODEL"}
claude "$@" >"$out" 2>&1
finish $? "$out"
