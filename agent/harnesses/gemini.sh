#!/usr/bin/env bash
# Gemini CLI on a Google plan (PRD 1.30, AH5). Contract: see common.sh.
# Non-interactive prompt, tool calls approved (the batch folder is its workspace).
# Prove it with make agent-check HARNESS=gemini before switching (AH7).
set -u
. "$(dirname "$0")/common.sh"
need gemini
out=$(mktemp)
gemini --yolo ${JAP_MODEL:+-m "$JAP_MODEL"} -p "$(cat "$1")" >"$out" 2>&1
finish $? "$out"
