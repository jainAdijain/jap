#!/usr/bin/env bash
# OpenAI Codex CLI on a ChatGPT plan (PRD 1.30, AH5). Contract: see common.sh.
# Non-interactive exec, sandbox limited to writing in the batch folder, no approvals asked.
# Its record of the session (O17) is its --json output: items, and token usage per turn.
# Prove it with make agent-check HARNESS=codex before switching (AH7).
set -u
. "$(dirname "$0")/common.sh"
need codex
out=$(mktemp)
set -- exec --full-auto --skip-git-repo-check -C "$PWD" ${JAP_MODEL:+-m "$JAP_MODEL"}
if traced; then
	codex "$@" --json "$(cat "$1")" 2>>"$out" | record codex "$out"
	codes=("${PIPESTATUS[@]}")
	rc=${codes[0]}
	[ "$rc" -eq 0 ] && [ "${codes[1]}" -eq 3 ] && rc=1
else
	codex "$@" "$(cat "$1")" >"$out" 2>&1
	rc=$?
fi
finish "$rc" "$out"
