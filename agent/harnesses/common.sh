# Shared by the harness adapters (PRD 1.30, AH5). Sourced, not run.
#
# An adapter is called by agent/run.sh from inside one batch folder (in.json and the job's
# prompt are there) as `<adapter> <prompt file>`, with JAP_MODEL set when a model was chosen.
# It runs the harness once, without a chat window, prints what the harness printed, and exits
#   0   the harness finished (run.sh then looks for the file the prompt asked for)
#   75  the plan's usage limit was reached: run.sh starts nothing more until the next run
#   127 the harness's command line tool is not installed
#   any other code: the harness failed
# The harness should be able to read and write files in the batch folder only, and needs no
# shell, no web and no access to JAP itself: run.sh does every export and import.

# need <command>: exit 127 with a hint when the tool is missing.
need() {
	command -v "$1" >/dev/null 2>&1 && return 0
	echo "$1 is not installed or not on PATH (launchd uses your login shell's PATH)"
	exit 127
}

# finish <exit code> <output file>: print the output, then exit 75 when it says the plan's
# limit was reached, else with the harness's own code.
finish() {
	cat "$2"
	if grep -Eiq 'usage limit|rate limit|limit reached|reached your .*limit|quota|exceeded your|out of credits|too many requests' "$2"; then
		exit 75
	fi
	exit "$1"
}
