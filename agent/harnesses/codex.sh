#!/usr/bin/env bash
# OpenAI Codex CLI on a ChatGPT plan (PRD 1.30, AH5). Contract: see common.sh.
# Non-interactive exec, sandbox limited to writing in the batch folder, no approvals asked.
# Prove it with make agent-check HARNESS=codex before switching (AH7).
set -u
. "$(dirname "$0")/common.sh"
need codex
out=$(mktemp)
codex exec --full-auto --skip-git-repo-check -C "$PWD" ${JAP_MODEL:+-m "$JAP_MODEL"} \
	"$(cat "$1")" >"$out" 2>&1
finish $? "$out"
