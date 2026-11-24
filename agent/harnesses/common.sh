# Shared by the harness adapters (PRD 1.30, AH5). Sourced, not run.
#
# An adapter is called by agent/run.sh from inside one batch folder (in.json and the job's
# prompt are there) as `<adapter> <prompt file>`, with JAP_MODEL set when a model was chosen.
# It runs the harness once, without a chat window, prints what the harness said at the end,
# and exits
#   0   the harness finished (run.sh then looks for the file the prompt asked for)
#   75  the plan's usage limit was reached: run.sh starts nothing more until the next run
#   127 the harness's command line tool is not installed
#   any other code: the harness failed
# The harness should be able to read and write files in the batch folder only, and needs no
# shell, no web and no access to JAP itself: run.sh does every export and import.
#
# From PRD 1.33 (O17, level 3) an adapter also asks its harness for a record of the session
# where it has one (a JSON event stream on its output, or an export file) and hands it to
# record, which keeps the numbers in steps.json (each model request and tool call, with time
# and tokens) and drops the text. An adapter that cannot do this still works: the runner
# traces the session from outside. JAP_AGENT_TRACE=0 runs every harness as before 1.33.
# The trace service's key is never given to a harness, and no adapter switches on a
# harness's own telemetry (AH8).

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# What a harness says when its plan's limit is reached.
LIMIT_WORDS='usage limit|rate limit|limit reached|reached your .*limit|hit your .*limit|quota|exceeded your|out of credits|too many requests'

# need <command>: exit 127 with a hint when the tool is missing.
need() {
	command -v "$1" >/dev/null 2>&1 && return 0
	echo "$1 is not installed or not on PATH (launchd uses your login shell's PATH)"
	exit 127
}

# traced: whether to ask the harness for its record (needs python3 for agent/trace.py).
traced() { [ "${JAP_AGENT_TRACE:-1}" != 0 ] && command -v python3 >/dev/null 2>&1; }

# record <harness> <final message file> [record file] [when the harness exited]: the
# harness's record (its event stream on stdin, or the file) to steps.json, and its last words
# added to the message file ("" to add nothing). Exits 3 when the record itself says the
# session ended in an error.
record() {
	python3 "$AGENT_DIR/trace.py" read "$1" ${2:+--final "$2"} ${3:+--record "$3"} ${4:+--ended "$4"}
}

# finish <exit code> <final message file>: print the message, then exit 75 when the plan's
# limit was reached, else with the harness's own code. A limit is taken from the harness's
# record, or from its last words when it failed; never from words in a session that worked,
# which may quote a job description ("sales quota").
finish() {
	cat "$2"
	if [ -f steps.json ] && grep -q '"error": "usage_limit"' steps.json; then
		exit 75
	fi
	if [ "$1" -ne 0 ] && tail -c 2000 "$2" | grep -Eiq "$LIMIT_WORDS"; then
		exit 75
	fi
	exit "$1"
}
