#!/usr/bin/env bash
# The Mac's schedule for the reader and the writer (PRD 1.30, AH6), with launchd.
#
#   agent/schedule.sh install    (re)install both jobs at JAP_AGENT_READER_AT / _WRITER_AT
#   agent/schedule.sh remove     remove both jobs
#   agent/schedule.sh status     whether each is loaded, its time, its last run and that
#                                run's trace (sessions, time in the model, tokens, causes)
#
# Started by make ENV=live agent-schedule / agent-unschedule / agent-status. Each job runs
# `make ENV=<env> agent-run JOB=<job>` under /usr/bin/caffeinate -i, so the Mac cannot idle
# sleep while a run is going (a sleep mid-run broke the ssh to the live VM; PRD 1.30, AH).
# The command runs in a login shell, so the harness's command line tool is found on the
# same PATH as in Terminal. A DOCKER_HOST set in the scheduling environment is written into
# each plist, so the jobs reach the live stack on its VM when it lives there. The harness
# and model are read from the env file at run time, so after changing JAP_HARNESS nothing
# needs reinstalling; only a new time does. launchd runs a job missed while the Mac slept
# when it wakes, and make mac-wake wakes it before the daily search (W7).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACTION="${1:-status}"
ENV_NAME="${ENV:-live}"
DATA="${JAP_DATA_HOST_DIR:-data}"
case "$DATA" in /*) ;; *) DATA="$ROOT/$DATA" ;; esac
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

if [ "$(uname -s)" != Darwin ]; then
	echo "agent-schedule uses launchd and works on macOS only. Elsewhere, run"
	echo "  make ENV=$ENV_NAME agent-run JOB=reader   and   make ENV=$ENV_NAME agent-run JOB=writer"
	echo "from cron at the times you want."
	exit 1
fi

label() { echo "com.jap.$ENV_NAME.agent-$1"; }
plist() { echo "$AGENTS/$(label "$1").plist"; }

at_for() {
	case "$1" in
	reader) echo "${JAP_AGENT_READER_AT:-07:05}" ;;
	writer) echo "${JAP_AGENT_WRITER_AT:-07:30}" ;;
	esac
}

install_one() {
	local job=$1 at hour minute
	at=$(at_for "$job")
	case "$at" in [0-2][0-9]:[0-5][0-9]) ;; *)
		echo "JAP_AGENT_$(echo "$job" | tr a-z A-Z)_AT must be HH:MM, not '$at'"
		return 1
		;;
	esac
	hour=$((10#${at%%:*}))
	minute=$((10#${at##*:}))
	local envvars=""
	if [ -n "${DOCKER_HOST:-}" ]; then
		envvars=$(printf '\n\t<key>EnvironmentVariables</key>\n\t<dict><key>DOCKER_HOST</key><string>%s</string></dict>' "$DOCKER_HOST")
	fi
	mkdir -p "$AGENTS" "$DATA/agent"
	cat >"$(plist "$job")" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$(label "$job")</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/zsh</string><string>-lc</string>
		<string>cd '$ROOT' &amp;&amp; /usr/bin/caffeinate -i make ENV=$ENV_NAME agent-run JOB=$job</string>
	</array>
	<key>StartCalendarInterval</key>
	<dict><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>$minute</integer></dict>$envvars
	<key>StandardOutPath</key><string>$DATA/agent/launchd-$job.log</string>
	<key>StandardErrorPath</key><string>$DATA/agent/launchd-$job.log</string>
</dict>
</plist>
PLIST
	launchctl bootout "$DOMAIN" "$(plist "$job")" 2>/dev/null
	launchctl bootstrap "$DOMAIN" "$(plist "$job")" &&
		echo "$job: every day at $at (Mac time), $(label "$job")"
}

remove_one() {
	launchctl bootout "$DOMAIN" "$(plist "$1")" 2>/dev/null
	rm -f "$(plist "$1")"
	echo "$1: removed"
}

status_one() {
	local job=$1 last
	if launchctl print "$DOMAIN/$(label "$job")" >/dev/null 2>&1; then
		echo "$job: scheduled daily at $(at_for "$job") (installed time: $(
			/usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:Hour' "$(plist "$job")"
		):$(printf %02d "$(/usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:Minute' "$(plist "$job")")"))"
	else
		echo "$job: not scheduled"
	fi
	if [ -f "$DATA/agent/last-$job.log" ]; then
		echo "  last run: $(head -1 "$DATA/agent/last-$job.log")"
		echo "            $(grep -v '^[0-9:]* Trace: ' "$DATA/agent/last-$job.log" | tail -1)"
	fi
	# The last run's trace (PRD 1.33, O18): sessions, time in the model, tokens and causes.
	last=$(ls "$DATA"/agent/traces/*-"$job".json 2>/dev/null | tail -1)
	if [ -n "$last" ] && command -v python3 >/dev/null 2>&1; then
		python3 "$ROOT/agent/trace.py" last "$last" | sed 's/^/  last trace: /'
	fi
}

case "$ACTION" in
install) install_one reader && install_one writer &&
	echo "Pause the Claude app's scheduled JAP tasks, if they are still on, so nothing runs twice." ;;
remove) remove_one reader && remove_one writer ;;
status) status_one reader && status_one writer ;;
*)
	echo "usage: agent/schedule.sh install|remove|status"
	exit 2
	;;
esac
