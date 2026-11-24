#!/usr/bin/env bash
# Devin's command line tool on a Devin plan (PRD 1.33, AH5). Contract: see common.sh.
# Print mode with edits in the batch folder approved (accept-edits), and JAP's own settings
# (devin.config.json) in place of yours: no shell and no web tools, a fetch and any MCP tool
# denied, no subagents, no rules or servers taken over from other tools, nothing under your
# home folder readable (so not this checkout's .env files either), and in.json, prompt.md
# and failures.md not writable. Whatever is left that would need an approval is refused,
# since nobody is there to give one. Your own settings are not used because they may allow
# more (one allowed every web fetch). All of this was tried against the tool on 23 Nov 2026.
# The tool writes settings of its own into the file it is given (your organisation, its
# first-run marks), so it gets a copy and devin.config.json stays as it is in git.
# Because reads under the home folder are denied, the batch folder must be outside it, as
# it is by default (the temp folder).
# Its record of the session (O17) is the file --export writes, read once the session is over
# and then deleted: it holds the session's text, so it is kept outside the batch folder.
# The tool is `devin` on PATH, else the copy inside Devin Desktop on a Mac; JAP_DEVIN_BIN
# names another. Sign in once with `devin auth login`. ACP_BACKEND is taken out of the
# environment: Devin Desktop sets it in its own terminals, and with it the tool looks for a
# different sign-in and says "Not logged in".
# Prove it with make agent-check HARNESS=devin before switching (AH7).
set -u
. "$(dirname "$0")/common.sh"
DEVIN="${JAP_DEVIN_BIN:-devin}"
APP="/Applications/Devin.app/Contents/Resources/app/extensions/windsurf/devin/bin/devin"
command -v "$DEVIN" >/dev/null 2>&1 || { [ -x "$APP" ] && DEVIN="$APP"; }
need "$DEVIN"
unset ACP_BACKEND
case "$(pwd -P)/" in "$(cd "$HOME" && pwd -P)"/*)
	echo "The Devin adapter denies reads under your home folder, and this batch folder is in it."
	echo "Leave JAP_AGENT_WORK_DIR unset, or point it outside $HOME."
	exit 2
	;;
esac
out=$(mktemp)
cfg=$(mktemp)
cp "$AGENT_DIR/harnesses/devin.config.json" "$cfg"
set -- -p --prompt-file "$1" --config "$cfg" \
	--permission-mode accept-edits --respect-workspace-trust false \
	${JAP_MODEL:+--model "$JAP_MODEL"}
if traced; then
	kept=$(mktemp)
	"$DEVIN" "$@" --export "$kept" >"$out" 2>&1
	rc=$?
	record devin "" "$kept" "$(date +%s)"
	rm -f "$kept"
else
	"$DEVIN" "$@" >"$out" 2>&1
	rc=$?
fi
rm -f "$cfg"
finish "$rc" "$out"
