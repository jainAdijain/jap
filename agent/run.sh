#!/usr/bin/env bash
# The runner (PRD 1.30, AH4): does JAP's AI work on whichever harness JAP_HARNESS names.
#
#   agent/run.sh reader     read unread postings and settle possible duplicates (R1 to R8)
#   agent/run.sh writer     answer jobs at Needs you, write cover letters, read new resumes
#                           (W2 to W12), then a checker pass before anything goes back (W8)
#
# Usually started by make agent-run, or by the Mac's scheduler (make agent-schedule). The
# runner does all the organising a single AI session used to do: it exports each batch, starts
# the harness once per batch through its adapter (agent/harnesses/<name>.sh), imports what
# comes back, runs the reader's shards in parallel and a leftover pass, and stops when the
# plan's usage limit is reached. The harness only ever sees one batch folder: in.json from the
# export, the job's prompt (agent/jobs/) and the file it writes. It never runs JAP's commands,
# never reads the database and needs no shell.
#
# Settings (from the environment's .env file through make, or the environment):
#   JAP_HARNESS            claude (default), codex, gemini, opencode, or any adapter's name
#   JAP_HARNESS_MODEL      model for that harness; empty = the harness's own default
#   JAP_CHECKER_HARNESS    harness for the writer's checker pass; default = JAP_HARNESS,
#                          none = import the writer's answers unchecked (not recommended)
#   JAP_CHECKER_MODEL      model for the checker; default = JAP_HARNESS_MODEL
#   JAP_AGENT_SHARDS       reader shards run in parallel (8)
#   JAP_AGENT_BATCH        postings per reader batch (10)
#   JAP_AGENT_TIMEOUT_MIN  minutes one harness session may take before it is stopped (20)
#   JAP_AGENT_WORK_DIR     where batch folders go; default outside the repo, in the temp
#                          folder, so a harness that reads instruction files from the git
#                          tree it runs in (AGENTS.md, CLAUDE.md) finds none of JAP's
#   MAX_BATCHES            stop each shard after this many batches; 0 = until done (0)
# Exit code: 0 done or nothing to do, 75 usage limit reached, 1 something failed.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JOB="${1:-}"
case "$JOB" in reader | writer) ;; *)
	echo "usage: agent/run.sh reader|writer"
	exit 2
	;;
esac

ENV_NAME="${ENV:-local}"
HARNESS="${JAP_HARNESS:-claude}"
MODEL="${JAP_HARNESS_MODEL:-}"
CHECKER="${JAP_CHECKER_HARNESS:-$HARNESS}"
CHECKER_MODEL="${JAP_CHECKER_MODEL:-$MODEL}"
SHARDS="${JAP_AGENT_SHARDS:-8}"
BATCH="${JAP_AGENT_BATCH:-10}"
TIMEOUT_MIN="${JAP_AGENT_TIMEOUT_MIN:-20}"
MAX_BATCHES="${MAX_BATCHES:-0}"
DATA="${JAP_DATA_HOST_DIR:-data}"
case "$DATA" in /*) ;; *) DATA="$ROOT/$DATA" ;; esac
# How the runner calls JAP; the test swaps in a stand-in.
JAP_MAKE="${JAP_MAKE:-make -s --no-print-directory -C $ROOT ENV=$ENV_NAME}"

for h in "$HARNESS" "$CHECKER"; do
	[ "$h" = none ] && continue
	if [ ! -x "$ROOT/agent/harnesses/$h.sh" ]; then
		echo "No adapter for harness '$h': add agent/harnesses/$h.sh (see common.sh there)"
		exit 2
	fi
done

STAMP="$(date +%Y%m%d-%H%M%S)-$$"
WORK="${JAP_AGENT_WORK_DIR:-${TMPDIR:-/tmp}/jap-agent-$ENV_NAME}"
WORK="${WORK%/}"
RUN="$WORK/$STAMP-$JOB"
mkdir -p "$RUN" "$DATA/agent/runs"
LOG="$RUN/run.log"
log() { echo "$(date +%H:%M:%S) $*" | tee -a "$LOG"; }

# One run of each job at a time. A lock older than 3 hours is from a run that died.
LOCK="$DATA/agent/$JOB.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
	if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ]; then
		rm -rf "$LOCK" && mkdir "$LOCK"
	else
		log "Another $JOB run is still going ($LOCK). Nothing started."
		exit 0
	fi
fi
trap 'rm -rf "$LOCK"' EXIT

# Batch folders (redacted job text) are kept 3 days, run logs 30.
find "$WORK" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +3 -exec rm -rf {} + 2>/dev/null
find "$DATA/agent/runs" -name '*.log' -mtime +30 -exec rm -f {} + 2>/dev/null

# kill_tree <pid>: stop a process and everything it started (a harness runs helpers).
kill_tree() {
	local c
	for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done
	kill -TERM "$1" 2>/dev/null
}

# call_harness <name> <model> <prompt> <folder>: one session in that folder, at most
# TIMEOUT_MIN minutes. Returns the adapter's code (75 = usage limit, 124 = timed out).
call_harness() {
	local name=$1 model=$2 prompt=$3 dir=$4 pid wd rc
	cp "$prompt" "$dir/prompt.md"
	(cd "$dir" && JAP_MODEL="$model" exec "$ROOT/agent/harnesses/$name.sh" prompt.md) \
		>"$dir/harness.log" 2>&1 &
	pid=$!
	(
		sleep $((TIMEOUT_MIN * 60))
		echo timeout >"$dir/timed-out"
		kill_tree "$pid"
	) >/dev/null 2>&1 &
	wd=$!
	wait "$pid"
	rc=$?
	kill_tree "$wd"
	wait "$wd" 2>/dev/null
	[ -e "$dir/timed-out" ] && rc=124
	return "$rc"
}

# has <file> <key>: the exported JSON holds at least one entry with that id key.
has() { grep -q "\"$2\"" "$1"; }

# --- reader -------------------------------------------------------------------------------

# reader_worker <i> <n>: export, read, import, until a batch is empty or saves nothing.
reader_worker() {
	local i=$1 n=$2 k=0 b rc saved
	while :; do
		[ -e "$RUN/stop" ] && return 0
		k=$((k + 1))
		b="$RUN/reader-$i-of-$n/batch-$k"
		mkdir -p "$b"
		if ! $JAP_MAKE reader-export SHARD="$i/$n" LIMIT="$BATCH" >"$b/in.json" 2>"$b/export.err"; then
			log "shard $i/$n: export failed: $(tail -1 "$b/export.err")"
			return 1
		fi
		has "$b/in.json" posting_id || has "$b/in.json" pair_id || return 0
		call_harness "$HARNESS" "$MODEL" "$ROOT/agent/jobs/reader.md" "$b"
		rc=$?
		if [ "$rc" -eq 75 ]; then
			log "shard $i/$n: the $HARNESS plan's usage limit is reached; the rest waits for the next run"
			touch "$RUN/stop"
			return 0
		fi
		if [ ! -s "$b/out.json" ]; then
			log "shard $i/$n batch $k: $HARNESS wrote no out.json (exit $rc, see $b/harness.log)"
			return 1
		fi
		$JAP_MAKE reader-import FILE="$b/out.json" >"$b/import.txt" 2>&1
		saved=$(sed -n 's/^saved \([0-9][0-9]*\) of [0-9][0-9]*$/\1/p' "$b/import.txt" | tail -1)
		log "shard $i/$n batch $k: $(tail -1 "$b/import.txt")"
		[ "${saved:-0}" -gt 0 ] || return 0
		[ "$MAX_BATCHES" -gt 0 ] && [ "$k" -ge "$MAX_BATCHES" ] && return 0
	done
}

reader_status() { $JAP_MAKE reader-status 2>/dev/null | tail -1; }
field() { echo "$1" | sed -n "s/.*$2=\([0-9][0-9]*\).*/\1/p"; }

run_reader() {
	local st tries=0 i failed=0
	st=$(reader_status)
	[ -n "$st" ] || {
		log "JAP did not answer (is the $ENV_NAME stack up?)"
		return 1
	}
	while [ "$(field "$st" run_active)" = 1 ] && [ "$tries" -lt 10 ]; do
		log "A search is still running; waiting a minute ($st)"
		sleep 60
		tries=$((tries + 1))
		st=$(reader_status)
	done
	log "Reader on $HARNESS${MODEL:+ ($MODEL)}: $st"
	if [ "$(field "$st" unread)" = 0 ] && [ "$(field "$st" pairs)" = 0 ]; then
		log "Nothing to read"
		return 0
	fi
	i=1
	while [ "$i" -le "$SHARDS" ]; do
		reader_worker "$i" "$SHARDS" &
		i=$((i + 1))
	done
	for pid in $(jobs -p); do wait "$pid" || failed=1; done
	if [ ! -e "$RUN/stop" ] && [ "$MAX_BATCHES" = 0 ]; then
		st=$(reader_status)
		if [ "$(field "$st" unread)" != 0 ] || [ "$(field "$st" pairs)" != 0 ]; then
			log "Leftovers after the shards ($st): one more pass"
			reader_worker 1 1 || failed=1
		fi
	fi
	st=$(reader_status)
	log "Done. Saved $(cat "$RUN"/reader-*/batch-*/import.txt 2>/dev/null |
		sed -n 's/^saved \([0-9]*\) of.*/\1/p' | awk '{s+=$1} END {print s+0}'). Now: $st"
	[ -e "$RUN/stop" ] && return 75
	return "$failed"
}

# --- writer -------------------------------------------------------------------------------

run_writer() {
	local st b="$RUN/writer" rc
	st=$($JAP_MAKE writer-status 2>/dev/null | tail -1)
	[ -n "$st" ] || {
		log "JAP did not answer (is the $ENV_NAME stack up?)"
		return 1
	}
	log "Writer on $HARNESS${MODEL:+ ($MODEL)}, checker on $CHECKER: $st"
	if [ "$(field "$st" jobs)" = 0 ] && [ "$(field "$st" resumes)" = 0 ]; then
		log "Nothing to write"
		return 0
	fi
	mkdir -p "$b"
	if ! $JAP_MAKE writer-export >"$b/in.json" 2>"$b/export.err"; then
		log "export failed: $(tail -1 "$b/export.err")"
		return 1
	fi
	call_harness "$HARNESS" "$MODEL" "$ROOT/agent/jobs/writer.md" "$b"
	rc=$?
	[ "$rc" -eq 75 ] && log "The $HARNESS plan's usage limit is reached; nothing imported" && return 75
	if [ ! -s "$b/draft.json" ]; then
		log "$HARNESS wrote no draft.json (exit $rc, see $b/harness.log)"
		return 1
	fi
	if [ "$CHECKER" = none ]; then
		cp "$b/draft.json" "$b/out.json"
		log "No checker (JAP_CHECKER_HARNESS=none): importing the draft as written"
	else
		mkdir -p "$b/check"
		cp "$b/in.json" "$b/draft.json" "$b/check/"
		call_harness "$CHECKER" "$CHECKER_MODEL" "$ROOT/agent/jobs/checker.md" "$b/check"
		rc=$?
		[ "$rc" -eq 75 ] && log "The $CHECKER plan's usage limit is reached in the check; nothing imported" && return 75
		if [ ! -s "$b/check/out.json" ]; then
			log "The checker wrote no out.json (exit $rc); nothing imported, so nothing unchecked goes on"
			return 1
		fi
		cp "$b/check/out.json" "$b/out.json"
		[ -s "$b/check/findings.txt" ] && sed 's/^/  checker: /' "$b/check/findings.txt" | tee -a "$LOG"
	fi
	$JAP_MAKE writer-import FILE="$b/out.json" >"$b/import.txt" 2>&1
	sed 's/^/  import: /' "$b/import.txt" | tee -a "$LOG"
	log "Done. $($JAP_MAKE writer-status 2>/dev/null | tail -1)"
}

log "JAP $JOB run on $ENV_NAME, folder $RUN"
"run_$JOB"
rc=$?
cp "$LOG" "$DATA/agent/last-$JOB.log"
cp "$LOG" "$DATA/agent/runs/$STAMP-$JOB.log"
exit "$rc"
