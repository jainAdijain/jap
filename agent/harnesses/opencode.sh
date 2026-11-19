#!/usr/bin/env bash
# opencode with any provider it is signed in to (PRD 1.30, AH5). Contract: see common.sh.
# JAP_MODEL is provider/model, for example anthropic/claude-sonnet-4-5.
# Prove it with make agent-check HARNESS=opencode before switching (AH7).
set -u
. "$(dirname "$0")/common.sh"
need opencode
out=$(mktemp)
opencode run ${JAP_MODEL:+-m "$JAP_MODEL"} "$(cat "$1")" >"$out" 2>&1
finish $? "$out"
