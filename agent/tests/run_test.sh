#!/usr/bin/env bash
# The runner (agent/run.sh, PRD 1.30 AH4) against a stand-in JAP (fake-jap.sh) and a stand-in
# harness (harnesses/fake.sh): every posting and pair read across 3 shards and a leftover
# pass, writer answers checked and imported, a usage limit stops the run with exit 75, and an
# unknown harness is refused. Then the trace (agent/trace.py, PRD 1.33 O16 to O21): a trace per
# run and per session with model requests, tool calls and tokens, a cause for each kind of
# failure, no text in any of it, and each harness's record read from a fixture. Then what
# 1.34 adds: each run's trace handed to JAP to send, kept and handed over again when that
# fails (O22), and the writer's and checker's reasons in codes (O24). Then 1.35: calls to JAP
# tried again when they did not reach it, the causes slept and unreachable, and the
# schedule's hourly tries (AH4, AH6).
# Needs bash and python3 only. Run with make test-agent.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP" "$ROOT/agent/harnesses/zz-silent-$$.sh"' EXIT
fails=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

export FAKE_STATE="$TMP/state.json" JAP_MAKE="$HERE/fake-jap.sh" JAP_DATA_HOST_DIR="$TMP/data"
export JAP_AGENT_WORK_DIR="$TMP/work" JAP_FAILURES_FILE="$TMP/failures.md"
export JAP_HARNESS=fake JAP_AGENT_SHARDS=3 JAP_AGENT_BATCH=4 JAP_AGENT_TIMEOUT_MIN=1
state() { python3 -c "import json; print(json.dumps(json.load(open('$FAKE_STATE'))$1))"; }

python3 -c "import json; json.dump({'unread': [str(i) for i in range(1, 30)],
  'pairs': ['p1', 'p2'], 'jobs': ['a1', 'a2']}, open('$FAKE_STATE', 'w'))"
JAP_TRACE_ENDPOINT=https://trace.example JAP_TRACE_HEADERS=x-api-key=k \
	"$ROOT/agent/run.sh" reader >"$TMP/reader.out" 2>&1
rc=$?
check "reader exits 0" '[ "$rc" = 0 ]'
check "the trace service's settings never reach a harness" '[ ! -e "$FAKE_STATE.saw-trace-key" ]'
check "reader read every posting" '[ "$(state "[\"unread\"]")" = "[]" ]'
check "reader settled every pair" '[ "$(state "[\"pairs\"]")" = "[]" ]'
check "reader ran 3 shards" '[ "$(ls -d "$TMP"/work/*-reader/reader-*-of-3 | wc -l | tr -d " ")" = 3 ]'
check "reader says what it saved" 'grep -q "Done. Saved 31" "$TMP/reader.out"'

# 1.40 (AH4): one shard for each batch of postings to read, at least one, at most the setting.
python3 -c "import json; json.dump({'unread': [str(i) for i in range(1, 6)], 'pairs': [],
  'jobs': ['a1', 'a2']}, open('$FAKE_STATE', 'w'))"
JAP_AGENT_WORK_DIR="$TMP/few" JAP_DATA_HOST_DIR="$TMP/few-data" "$ROOT/agent/run.sh" reader >"$TMP/few.out" 2>&1
check "5 postings in batches of 4 start 2 shards, not 3, and read them all" '[ "$(ls -d "$TMP"/few/*-reader/reader-*-of-2 | wc -l | tr -d " ")" = 2 ] && [ ! -e "$(ls -d "$TMP"/few/*-reader | head -1)/reader-1-of-3" ] && [ "$(state "[\"unread\"]")" = "[]" ] && grep -q "2 shards of up to 4 postings a batch" "$TMP/few.out"'
python3 -c "import json; json.dump({'unread': [], 'pairs': ['p3'], 'jobs': ['a1', 'a2']},
  open('$FAKE_STATE', 'w'))"
JAP_AGENT_WORK_DIR="$TMP/pairs" JAP_DATA_HOST_DIR="$TMP/pairs-data" "$ROOT/agent/run.sh" reader >"$TMP/pairs.out" 2>&1
check "pairs only: one shard, which settles them" '[ "$(ls -d "$TMP"/pairs/*-reader/reader-*-of-* | wc -l | tr -d " ")" = 1 ] && [ -d "$(ls -d "$TMP"/pairs/*-reader | head -1)/reader-1-of-1" ] && [ "$(state "[\"pairs\"]")" = "[]" ]'

"$ROOT/agent/run.sh" reader >"$TMP/reader2.out" 2>&1
check "nothing to read" 'grep -q "Nothing to read" "$TMP/reader2.out"'
check "run logs kept in the data folder" '[ -s "$TMP/data/agent/last-reader.log" ] && [ "$(ls "$TMP"/data/agent/runs | wc -l | tr -d " ")" = 2 ]'

"$ROOT/agent/run.sh" writer >"$TMP/writer.out" 2>&1
rc=$?
check "writer exits 0" '[ "$rc" = 0 ]'
check "writer answered both jobs" '[ "$(state "[\"jobs\"]")" = "[]" ]'
check "checker findings in the log" 'grep -q "checker: a1: ok" "$TMP/writer.out"'

python3 -c "import json; json.dump({'unread': ['1', '2'], 'pairs': [], 'jobs': ['a3']},
  open('$FAKE_STATE', 'w'))"
FAKE_LIMIT=1 "$ROOT/agent/run.sh" reader >"$TMP/limit.out" 2>&1
rc=$?
check "usage limit exits 75" '[ "$rc" = 75 ]'
check "usage limit leaves postings unread" '[ "$(state "[\"unread\"]")" = "[\"1\", \"2\"]" ]'
FAKE_LIMIT=1 "$ROOT/agent/run.sh" writer >"$TMP/limit-w.out" 2>&1
rc=$?
check "writer usage limit exits 75, imports nothing" '[ "$rc" = 75 ] && [ "$(state "[\"jobs\"]")" = "[\"a3\"]" ]'

echo "lessons" >"$JAP_FAILURES_FILE"
python3 -c "import json; json.dump({'unread': ['9'], 'pairs': [], 'jobs': ['a4']},
  open('$FAKE_STATE', 'w'))"
"$ROOT/agent/run.sh" reader >"$TMP/failures.out" 2>&1
check "failures.md reaches the batch folder" '[ -f "$TMP"/work/*-reader/reader-*-of-*/batch-1/failures.md ]'
"$ROOT/agent/run.sh" writer >"$TMP/failures-w.out" 2>&1
check "failures.md reaches the writer folder" '[ -f "$TMP"/work/*-writer/writer/failures.md ]'
check "failures.md reaches the checker folder" '[ -f "$TMP"/work/*-writer/writer/check/failures.md ]'

JAP_HARNESS=nosuch "$ROOT/agent/run.sh" reader >"$TMP/nosuch.out" 2>&1
rc=$?
check "unknown harness refused" '[ "$rc" = 2 ] && grep -q "No adapter" "$TMP/nosuch.out"'

# --- the trace (PRD 1.33) -------------------------------------------------------------------
TRACES="$TMP/data/agent/traces"
# trace <file> <python expression over t>: a value from a trace file.
trace() { python3 -c "import json,sys; t=json.load(open(sys.argv[1])); print($2)" "$1"; }
first=$(ls "$TRACES"/*-reader.json | head -1)
check "a trace file for every run" '[ "$(ls "$TRACES" | wc -l | tr -d " ")" = "$(ls "$TMP"/data/agent/runs | wc -l | tr -d " ")" ]'
check "run trace: every session, with what was saved" '[ "$(trace "$first" "t[\"totals\"][\"sessions\"], t[\"totals\"][\"saved\"], t[\"totals\"][\"pairs_settled\"], t[\"before\"][\"unread\"], t[\"after\"][\"unread\"]")" = "9 29 2 29 0" ]'
check "session trace: model requests, tool calls and tokens" '[ "$(trace "$first" "[(s[\"level\"], s[\"totals\"][\"model_requests\"], s[\"totals\"][\"tool_calls\"], s[\"tokens\"][\"in\"], s[\"tokens\"][\"out\"], s[\"model\"]) for s in t[\"sessions\"]][0]")" = "(3, 3, 2, 6300, 21, '"'"'fake-1'"'"')" ]'
check "session trace: which tool touched which file" '[ "$(trace "$first" "sorted(set((x[\"tool\"], x[\"file\"]) for s in t[\"sessions\"] for x in s[\"steps\"] if x[\"kind\"] == \"tool\"))")" = "[('"'"'Read'"'"', '"'"'in.json'"'"'), ('"'"'Write'"'"', '"'"'out.json'"'"')]" ]'
check "session trace beside the batch" '[ -s "$(ls -d "$TMP"/work/*-reader/reader-1-of-3/batch-1 | head -1)/trace.json" ]'
check "run log: tokens per batch and a run summary" 'grep -q "saved 4 of 4 · .* 3 model requests · 2 tool calls · 6,300 tokens in, 21 out · fake-1" "$TMP/reader.out" && grep -q "^[0-9:]* Trace: 9 sessions · " "$TMP/reader.out"'
check "writer run: writer and checker sessions, findings counted" '[ "$(trace "$(ls "$TRACES"/*-writer.json | head -1)" "[(s[\"role\"], s[\"items\"].get(\"findings\", s[\"items\"].get(\"answered\"))) for s in t[\"sessions\"]]")" = "[('"'"'writer'"'"', {'"'"'ready_for_review'"'"': 2}), ('"'"'checker'"'"', {'"'"'ok'"'"': 2, '"'"'fixed'"'"': 0, '"'"'held'"'"': 0, '"'"'left_out'"'"': 0})]" ]'
check "no text in any trace file" '! grep -rq "SECRET\|quota\|fake harness done" "$TRACES" "$TMP"/work/*/*/*/trace.json "$TMP"/work/*/*/*/steps.json "$TMP"/work/*/writer/trace.json "$TMP"/work/*/writer/check/trace.json'
check "the batch did hold that text" 'grep -q "SECRET-POSTING-TEXT" "$(ls -d "$TMP"/work/*-reader/reader-1-of-3/batch-1 | head -1)/in.json"'
version_of() { sed -n 's/.*· version \([0-9.]*\),.*/\1/p' "$ROOT/agent/jobs/$1.md" | head -1; }
versions=$(trace "$first" "sorted(set((s['prompt_version'], s['contract_version']) for s in t['sessions']))")
versions_w=$(trace "$(ls "$TRACES"/*-writer.json | head -1)" "[s['prompt_version'] for s in t['sessions']]")
check "session trace: the job prompt's version and the contract version (O20)" '[ -n "$(version_of reader)" ] && [ "$versions" = "[('"'"'$(version_of reader)'"'"', 1)]" ] && [ "$versions_w" = "['"'"'$(version_of writer)'"'"', '"'"'$(version_of checker)'"'"']" ]'
# O22: every run so far handed its trace to JAP, once, and none is left marked as unsent.
handed() { for f in "$TRACES"/*.json; do grep -qx "$(basename "$f")" "$FAKE_STATE.sent" || return 1; done; }
check "each run's trace is handed to JAP once, and the run log says it was sent" 'handed && [ -z "$(sort "$FAKE_STATE.sent" | uniq -d)" ] && [ -z "$(ls "$TRACES" | grep -v "\.json$")" ] && grep -q "Trace sent to the trace service (10 spans)" "$TMP/reader.out"'

# One fresh data folder per case, so each case's trace is the only one there.
case_run() { # case_run <name> <state> <job> [VAR=value ...]
	local name=$1 state=$2 job=$3
	shift 3
	python3 -c "import json; json.dump($state, open('$FAKE_STATE', 'w'))"
	env JAP_DATA_HOST_DIR="$TMP/$name" JAP_AGENT_SHARDS=1 MAX_BATCHES=1 "$@" \
		"$ROOT/agent/run.sh" "$job" >"$TMP/$name.out" 2>&1
	rc=$?
	got="$TMP/$name/agent/traces/$(ls "$TMP/$name/agent/traces" | head -1)"
}
ONE="{'unread': ['1'], 'pairs': [], 'jobs': ['a9']}"
causes() { trace "$got" "sorted(t[\"totals\"][\"causes\"].items()), [s[\"level\"] for s in t[\"sessions\"]]"; }

case_run quota "$ONE" reader FAKE_SAY="I read 1 posting about a sales quota"
check "a session that worked and mentions a quota is not a usage limit" '[ "$rc" = 0 ] && [ "$(state "[\"unread\"]")" = "[]" ] && [ "$(causes)" = "[] [3]" ]'
case_run norecord "$ONE" reader FAKE_RECORD=0
check "a harness with no record still gets level 2, marked as an estimate" '[ "$rc" = 0 ] && [ "$(trace "$got" "[(s[\"level\"], s[\"tokens\"][\"estimate\"], s[\"tokens\"][\"in\"] > 0, s[\"seconds\"] >= 0, s[\"items\"][\"saved\"]) for s in t[\"sessions\"]]")" = "[(2, True, True, True, 1)]" ] && grep -q "an estimate: the harness gave no record" "$TMP/norecord.out"'
case_run off "$ONE" reader JAP_AGENT_TRACE=0
check "JAP_AGENT_TRACE=0: no record asked for, the run still traced at level 2" '[ "$rc" = 0 ] && [ "$(causes)" = "[] [2]" ] && [ ! -e "$(ls -d "$TMP"/work/*-reader/reader-1-of-1/batch-1 | tail -1)/steps.json" ]'
case_run limit "$ONE" reader FAKE_LIMIT=1
check "cause usage_limit" '[ "$rc" = 75 ] && [ "$(causes)" = "[('"'"'usage_limit'"'"', 1)] [2]" ]'
case_run signedout "$ONE" reader FAKE_SIGNED_OUT=1
check "cause not_logged_in, named in the run log" '[ "$rc" = 1 ] && [ "$(causes)" = "[('"'"'not_logged_in'"'"', 1)] [2]" ] && grep -q "wrote no out.json (exit 1, cause not_logged_in," "$TMP/signedout.out"'
printf '#!/bin/sh\nexit 0\n' >"$ROOT/agent/harnesses/zz-silent-$$.sh" && chmod +x "$ROOT/agent/harnesses/zz-silent-$$.sh"
case_run silent "$ONE" reader JAP_HARNESS="zz-silent-$$"
check "cause no_output" '[ "$rc" = 1 ] && [ "$(causes)" = "[('"'"'no_output'"'"', 1)] [2]" ]'
printf '#!/bin/sh\nsleep 300\n' >"$ROOT/agent/harnesses/zz-silent-$$.sh"
case_run slow "$ONE" reader JAP_HARNESS="zz-silent-$$" JAP_AGENT_TIMEOUT_MIN=0
check "cause timeout" '[ "$rc" = 1 ] && [ "$(causes)" = "[('"'"'timeout'"'"', 1)] [2]" ]'
printf '#!/bin/sh\necho "API Error: Your computer went to sleep mid-response."\nexit 1\n' >"$ROOT/agent/harnesses/zz-silent-$$.sh"
case_run slept "$ONE" reader JAP_HARNESS="zz-silent-$$"
check "cause slept" '[ "$rc" = 1 ] && [ "$(causes)" = "[('"'"'slept'"'"', 1)] [2]" ]'
rm -f "$ROOT/agent/harnesses/zz-silent-$$.sh"
cat >"$TMP/broken-jap.sh" <<EOF
#!/bin/sh
case "\$1" in reader-export | writer-import) echo "make: *** [\$1] Error 1" >&2; exit 2 ;; esac
exec "$HERE/fake-jap.sh" "\$@"
EOF
chmod +x "$TMP/broken-jap.sh"
case_run noexport "$ONE" reader JAP_MAKE="$TMP/broken-jap.sh"
check "cause export_failed" '[ "$rc" = 1 ] && [ "$(causes)" = "[('"'"'export_failed'"'"', 1)] [2]" ]'
case_run noimport "$ONE" writer JAP_MAKE="$TMP/broken-jap.sh"
check "cause import_failed" '[ "$(trace "$got" "[(s[\"role\"], s[\"cause\"]) for s in t[\"sessions\"]]")" = "[('"'"'writer'"'"', '"'"'import_failed'"'"'), ('"'"'checker'"'"', None)]" ]'
# 1.35 (AH4): a call that did not reach JAP is tried again; the trace says why it failed.
cat >"$TMP/far-jap.sh" <<EOF
#!/bin/sh
# Fails the first FAR_FAILS calls of target FAR_TARGET as a lost ssh to the VM does, or hangs.
n=\$(cat "$TMP/far.count" 2>/dev/null || echo 0)
if [ "\$1" = "\$FAR_TARGET" ] && [ "\$n" -lt "\${FAR_FAILS:-99}" ]; then
	echo \$((n + 1)) >"$TMP/far.count"
	[ "\${FAR_HANG:-0}" = 1 ] && sleep 30
	echo "ssh_dispatch_run_fatal: Connection to 192.0.2.1 port 22: Operation timed out" >&2
	echo "make[1]: *** [\$1] Error 1" >&2
	exit 2
fi
exec "$HERE/fake-jap.sh" "\$@"
EOF
chmod +x "$TMP/far-jap.sh"
export JAP_AGENT_RETRY_S=0
rm -f "$TMP/far.count"
case_run farflaky "$ONE" reader JAP_MAKE="$TMP/far-jap.sh" FAR_TARGET=reader-export FAR_FAILS=1
check "an export that did not reach JAP is tried again, and the run goes on" '[ "$rc" = 0 ] && [ "$(state "[\"unread\"]")" = "[]" ] && [ "$(cat "$TMP/far.count")" = 1 ] && grep -q "reader-export did not reach JAP (unreachable, try 1 of 3)" "$TMP/farflaky.out" && [ "$(causes)" = "[] [3]" ]'
rm -f "$TMP/far.count"
case_run fardown "$ONE" reader JAP_MAKE="$TMP/far-jap.sh" FAR_TARGET=reader-export
check "cause unreachable: never reached after 3 tries, and the log says so" '[ "$rc" = 1 ] && [ "$(cat "$TMP/far.count")" = 3 ] && [ "$(causes)" = "[('"'"'unreachable'"'"', 1)] [2]" ] && grep -q "export failed (JAP was not reached: ssh to the stack or Docker failed)" "$TMP/fardown.out"'
rm -f "$TMP/far.count"
case_run farslept "$ONE" reader JAP_MAKE="$TMP/far-jap.sh" FAR_TARGET=reader-export JAP_AGENT_WOKE_AT=4000000000
check "cause slept: the Mac woke after the call began" '[ "$rc" = 1 ] && [ "$(causes)" = "[('"'"'slept'"'"', 1)] [2]" ]'
rm -f "$TMP/far.count"
started=$(date +%s)
case_run farhang "$ONE" reader JAP_MAKE="$TMP/far-jap.sh" FAR_TARGET=reader-export FAR_HANG=1 JAP_AGENT_CALL_S=1 JAP_AGENT_CALL_TRIES=2
check "a call that hangs is stopped at its limit and tried again, not waited on" '[ "$rc" = 1 ] && [ "$(cat "$TMP/far.count")" = 2 ] && [ "$(causes)" = "[('"'"'unreachable'"'"', 1)] [2]" ] && [ $(($(date +%s) - started)) -lt 25 ]'
rm -f "$TMP/far.count"
case_run farimport "$ONE" writer JAP_MAKE="$TMP/far-jap.sh" FAR_TARGET=writer-import FAR_FAILS=1
check "an import that did not reach JAP is tried again" '[ "$rc" = 0 ] && [ "$(state "[\"jobs\"]")" = "[]" ] && [ "$(cat "$TMP/far.count")" = 1 ]'
rm -f "$TMP/far.count"
case_run farstatus "$ONE" writer JAP_MAKE="$TMP/far-jap.sh" FAR_TARGET=writer-status
check "a run whose status never came back says why: unreachable" '[ "$rc" = 1 ] && [ "$(trace "$got" "t[\"cause\"], t[\"totals\"][\"sessions\"]")" = "unreachable 0" ] && grep -q "JAP did not answer (JAP was not reached: ssh to the stack or Docker failed)" "$TMP/farstatus.out"'
rm -f "$TMP/far.count"
cat >"$TMP/count-jap.sh" <<EOF
#!/bin/sh
case "\$1" in reader-export) echo x >>"$TMP/answered.count"; echo "Traceback: KeyError" >&2; exit 2 ;; esac
exec "$HERE/fake-jap.sh" "\$@"
EOF
chmod +x "$TMP/count-jap.sh"
case_run answered "$ONE" reader JAP_MAKE="$TMP/count-jap.sh"
check "an export JAP answered with an error is not tried again, and stays export_failed" '[ "$rc" = 1 ] && [ "$(wc -l <"$TMP/answered.count" | tr -d " ")" = 1 ] && [ "$(causes)" = "[('"'"'export_failed'"'"', 1)] [2]" ]'

# 1.35 (AH6): a scheduled run does not start in a dark wake, runs once on a day it finishes,
# and one that failed is tried again by the next hour's try.
SCHED="$TMP/sched"
sched() { # sched <name> [VAR=value ...]
	local name=$1
	shift
	env JAP_DATA_HOST_DIR="$SCHED" JAP_AGENT_SHARDS=1 JAP_AGENT_SCHEDULED=1 JAP_AGENT_ASLEEP=0 "$@" \
		"$ROOT/agent/run.sh" reader >"$TMP/$name.out" 2>&1
	rc=$?
}
runs() { ls "$SCHED/agent/runs" 2>/dev/null | wc -l | tr -d " "; }
python3 -c "import json; json.dump($ONE, open('$FAKE_STATE', 'w'))"
sched dark JAP_AGENT_ASLEEP=1
check "a scheduled run in a dark wake does not start and leaves no run log" '[ "$rc" = 0 ] && grep -q "dark wake" "$TMP/dark.out" && [ "$(runs)" = 0 ] && [ "$(state "[\"unread\"]")" = "[\"1\"]" ]'
rm -f "$TMP/far.count"
sched schedfail JAP_MAKE="$TMP/far-jap.sh" FAR_TARGET=reader-status
check "a scheduled run that failed is not marked done" '[ "$rc" = 1 ] && [ ! -e "$SCHED/agent/reader.done" ] && [ "$(runs)" = 1 ]'
sched schedok
check "the next try runs, and marks the day done" '[ "$rc" = 0 ] && [ "$(cat "$SCHED/agent/reader.done")" = "$(date +%F)" ] && [ "$(state "[\"unread\"]")" = "[]" ] && [ "$(runs)" = 2 ]'
python3 -c "import json; json.dump($ONE, open('$FAKE_STATE', 'w'))"
sched schedagain
check "a scheduled try on a day already done does nothing, quietly" '[ "$rc" = 0 ] && [ ! -s "$TMP/schedagain.out" ] && [ "$(runs)" = 2 ] && [ "$(state "[\"unread\"]")" = "[\"1\"]" ]'
env JAP_DATA_HOST_DIR="$SCHED" JAP_AGENT_SHARDS=1 "$ROOT/agent/run.sh" reader >"$TMP/byhand.out" 2>&1
check "a run by hand is not held back by the day being done" '[ "$?" = 0 ] && [ "$(state "[\"unread\"]")" = "[]" ]'
unset JAP_AGENT_RETRY_S

# 1.36 (AH10): long text leaves in.json for wrapped files under text/, whole.
case_run long "$ONE" reader FAKE_LONG=1
lb=$(ls -d "$TMP"/work/*-reader/reader-1-of-1/batch-1 | tail -1)
check "a long description goes to text/, wrapped, and in.json points to it" '[ "$rc" = 0 ] && grep -q "\"@text/postings-0-description_text.txt\"" "$lb/in.json" && [ "$(awk "length > 100" "$lb"/text/*.txt "$lb/in.json" | wc -l | tr -d " ")" = 0 ]'
check "the whole of it, to the last word" '[ "$(tr -d "\n " <"$lb/text/postings-0-description_text.txt")" = "$(python3 -c "print((\"SECRET POSTING\" + \" words \" * 1500)[:8990].replace(\" \", \"\") + \"LAST-WORD\")")" ]'
check "the trace: the longest line given, and the reader's fields left unknown" '[ "$(trace "$got" "[(s[\"sizes\"][\"longest_line\"] <= 100, s[\"items\"][\"fields\"], s[\"items\"][\"unknown\"]) for s in t[\"sessions\"]]")" = "[(True, 1, 1)]" ] && ! grep -q "LAST-WORD\|SECRET" "$got"'
case_run longw "$ONE" writer FAKE_LONG=1
lw=$(ls -d "$TMP"/work/*-writer/writer | tail -1)
check "the writer's resume goes to text/, and the checker gets the same files" '[ "$rc" = 0 ] && grep -q "\"resume\": \"@text/resume.txt\"" "$lw/in.json" && cmp -s "$lw/text/resume.txt" "$lw/check/text/resume.txt" && grep -q LAST-WORD "$lw/check/text/resume.txt"'

# 1.37 (W8, W13): no checker session for a draft of choices only; the jobs handed out go back
# with the answers, so JAP can note those left out.
case_run choices "$ONE" writer FAKE_OPTIONS=1
check "a draft of choices only goes in without a checker session" '[ "$rc" = 0 ] && [ "$(state "[\"jobs\"]")" = "[]" ] && grep -q "No checker session: the draft has only answers chosen from options" "$TMP/choices.out" && [ "$(trace "$got" "[s[\"role\"] for s in t[\"sessions\"]]")" = "['"'"'writer'"'"']" ]'
case_run choicesalways "$ONE" writer FAKE_OPTIONS=1 JAP_CHECKER_WHEN=always
check "JAP_CHECKER_WHEN=always checks it anyway" '[ "$rc" = 0 ] && [ "$(trace "$got" "[s[\"role\"] for s in t[\"sessions\"]]")" = "['"'"'writer'"'"', '"'"'checker'"'"']" ]'
J7=77777777-7777-4777-8777-777777777777 J8=88888888-8888-4888-8888-888888888888
case_run handed "{'unread': [], 'pairs': [], 'jobs': ['$J7', '$J8']}" writer FAKE_REASONS=silent
check "the jobs handed out go back with the answers, and JAP says which it noted as left out" '[ "$rc" = 0 ] && [ "$(state "[\"left_out\"]")" = "[\"$J7\"]" ] && grep -q "import: left out: 1 jobs not handed out again" "$TMP/handed.out" && [ "$(trace "$got" "[(s[\"role\"], s[\"items\"][\"left_out\"]) for s in t[\"sessions\"]]")" = "[('"'"'writer'"'"', 1), ('"'"'checker'"'"', 0)]" ]'

# 1.38 (O26): every export and import names its run, so JAP puts its span in the run's trace;
# a held job counts once in the run's reasons; a writer run carries no reader numbers.
rm -f "$FAKE_STATE.runs"
case_run stamped "$ONE" writer
stampof=$(basename "$got" -writer.json)
check "every export and import names the run it belongs to" '[ "$(cut -d" " -f1 "$FAKE_STATE.runs" | tr "\n" " ")" = "writer-export writer-import " ] && [ "$(cut -d" " -f2 "$FAKE_STATE.runs" | sort -u)" = "$stampof" ]'
check "a writer run carries no reader shards or batch size" '[ "$(trace "$got" "t[\"shards\"], t[\"batch\"]")" = "None None" ]'
case_run heldtwice "{'unread': [], 'pairs': [], 'jobs': ['$J7', '$J8']}" writer FAKE_REASONS=held
check "a job held by the writer and the checker counts once in the run" '[ "$(trace "$got" "t[\"totals\"][\"why\"]")" = "{'"'"'ai_banned'"'"': 2}" ]'
python3 - "$HERE/fixtures/devin-atif.json" "$TMP/devin-denied.json" <<'PY2'
import json, sys
d = json.load(open(sys.argv[1]))
step = next(s for s in d["steps"] if any(c["tool_call_id"] == "call-3" for c in s.get("tool_calls", [])))
step["tool_calls"].append({"tool_call_id": "call-9", "function_name": "edit",
                           "arguments": {"file_path": "in.json", "content": "SECRET"}})
step["observation"]["results"].append({"source_call_id": "call-9",
                                       "content": "SECRET words of another shape: not allowed"})
json.dump(d, open(sys.argv[2], "w"))
PY2
rm -rf "$TMP/read" && mkdir "$TMP/read"
(cd "$TMP/read" && python3 "$ROOT/agent/trace.py" read devin --record "$TMP/devin-denied.json" --ended 1795432711)
check "Devin: a write to in.json, which its settings deny, is counted refused whatever it answered" '[ "$(trace "$TMP/read/steps.json" "t[\"denied\"], [x[\"ok\"] for x in t[\"steps\"] if x.get(\"file\") == \"in.json\" and x[\"tool\"] == \"edit\"]")" = "2 [False]" ]'

case_run edited "$ONE" writer FAKE_EDIT_INPUT=1
edited=$(ls -d "$TMP"/work/*-writer/writer | tail -1)
check "a harness that changes its in.json is named, and the checker gets the export as JAP gave it" '[ "$rc" = 0 ] && grep -q edited_by_the_harness "$edited/in.json" && ! grep -q edited_by_the_harness "$edited/check/in.json" && grep -q "The writer changed its in.json" "$TMP/edited.out" && [ "$(trace "$got" "[(s[\"role\"], s[\"input_changed\"]) for s in t[\"sessions\"]], t[\"totals\"][\"input_changed\"]")" = "[('"'"'writer'"'"', True), ('"'"'checker'"'"', False)] 1" ]'
printf '#!/bin/sh\nexit 0\n' >"$TMP/dead-jap.sh" && chmod +x "$TMP/dead-jap.sh"
case_run noanswer "$ONE" reader JAP_MAKE="$TMP/dead-jap.sh"
check "JAP not answering is a run with no session and a cause" '[ "$rc" = 1 ] && [ "$(trace "$got" "t[\"cause\"], t[\"totals\"][\"sessions\"]")" = "no_answer 0" ]'
check "its trace waits, marked unsent" '[ -e "${got%.json}.unsent" ] && grep -q "Trace not sent: it is kept" "$TMP/noanswer.out"'

# O22: a trace JAP could not take is kept and handed over again at the start of the next run.
unsent() { ls "$TMP/$1/agent/traces" | grep -c "\.unsent$"; }
case_run senddown "$ONE" reader FAKE_TRACE=down
check "a trace that cannot be sent is kept, and the run ends as it would have" '[ "$rc" = 0 ] && [ "$(unsent senddown)" = 1 ] && [ "$(state "[\"unread\"]")" = "[]" ] && grep -q "Trace not sent (RuntimeError: the trace service answered 401): it is kept and handed over again at the start of the next run" "$TMP/senddown.out"'
case_run senddown "$ONE" reader
check "a mark under two minutes old is left alone: another run may be handing that trace over" '[ "$rc" = 0 ] && [ "$(unsent senddown)" = 1 ] && ! grep -q "earlier run" "$TMP/senddown.out"'
touch -t 202601010000 "$TMP"/senddown/agent/traces/*.unsent
waiting=$(basename "$(ls "$TMP"/senddown/agent/traces/*.unsent)" .unsent)
case_run senddown "$ONE" reader FAKE_TRACE=down
check "while JAP still cannot send, the earlier trace goes on waiting" '[ "$rc" = 0 ] && [ "$(unsent senddown)" = 2 ] && grep -q "The trace of an earlier run still cannot be sent (RuntimeError: the trace service answered 401); it waits" "$TMP/senddown.out"'
touch -t 202601010000 "$TMP"/senddown/agent/traces/*.unsent
case_run senddown "$ONE" reader
check "the next run hands the waiting traces over first, then its own" '[ "$rc" = 0 ] && [ "$(unsent senddown)" = 0 ] && grep -qx "$waiting.json" "$FAKE_STATE.sent" && [ "$(grep -c "The trace of an earlier run is sent" "$TMP/senddown.out")" = 2 ] && [ "$(ls "$TMP"/senddown/agent/traces | wc -l | tr -d " ")" = 4 ]'
case_run sendoff "$ONE" reader FAKE_TRACE=off
check "no trace service set: nothing kept, nothing said" '[ "$rc" = 0 ] && [ "$(unsent sendoff)" = 0 ] && ! grep -q "Trace sent\|Trace not sent" "$TMP/sendoff.out"'
started=$(date +%s)
case_run sendhang "$ONE" reader FAKE_TRACE=hang JAP_AGENT_SEND_S=1
check "a JAP that never answers holds the run a second, not for ever" '[ "$rc" = 0 ] && [ "$(unsent sendhang)" = 1 ] && [ $(($(date +%s) - started)) -lt 60 ]'

# O24: why the writer and the checker left a job out, held it or fixed it, in codes.
J1=11111111-1111-4111-8111-111111111111 J2=22222222-2222-4222-8222-222222222222
TWO="{'unread': [], 'pairs': [], 'jobs': ['$J1', '$J2']}"
PER_SESSION="[(s['role'], s['items']['left_out'], s['items']['reasons']['given'], s['items']['reasons']['outcomes'], s['items']['reasons']['why'], s['items']['reasons']['facts'], s['items']['reasons']['dropped']) for s in t['sessions']]"
case_run reasons "$TWO" writer FAKE_REASONS=1
w=$(ls -d "$TMP"/work/*-writer/writer | tail -1)
said=$(trace "$got" "$PER_SESSION")
want="[('writer', 1, True, {'left_out': 1}, {'fact_not_in_bank': 1}, {'salary': 1, 'notice_period': 1}, 4), ('checker', 0, True, {'fixed': 1}, {'unsupported_claim': 1}, {}, 0)]"
check "reasons.json: codes on the lists are kept, for ids of the export; the rest is dropped and counted" '[ "$rc" = 0 ] && [ "$said" = "$want" ]'
said=$(trace "$got" "t['totals']['left_out'], t['totals']['why'], t['totals']['facts'], t['totals']['reasons_dropped']")
want="1 {'fact_not_in_bank': 1, 'unsupported_claim': 1} {'salary': 1, 'notice_period': 1} 4"
check "the run's trace adds them up" '[ "$said" = "$want" ]'
check "the run log says why, a line per job, and how many were left out" 'grep -q "  why (writer): $J1: left out (fact_not_in_bank; the answer bank lacks: salary, notice_period)$" "$TMP/reasons.out" && grep -q "  why (checker): $J2: fixed (unsupported_claim)$" "$TMP/reasons.out" && grep -q "Trace: 2 sessions · .* · 1 job left out · " "$TMP/reasons.out"'
check "words in reasons.json reach neither a trace nor the run log" 'grep -q "SECRET reason" "$w/reasons.json" && ! grep -rq SECRET "$TMP/reasons/agent" "$w/trace.json" "$w/check/trace.json" "$TMP/reasons.out"'
check "the job left out stays at Needs you" '[ "$(state "[\"jobs\"]")" = "[\"$J1\"]" ]'
case_run noreasons "$TWO" writer FAKE_REASONS=silent
said=$(trace "$got" "$PER_SESSION")
want="[('writer', 1, False, {}, {}, {}, 0), ('checker', 0, False, {}, {}, {}, 0)]"
check "a harness that gives no reasons loses nothing: the runner still counts the job left out" '[ "$rc" = 0 ] && [ "$said" = "$want" ] && grep -q "  why (writer): 1 job left out with no reason given$" "$TMP/noreasons.out"'

# Each harness's own record of a session, read from a fixture (tests/fixtures; which were
# recorded from a run and which were built from documents is said in each reader).
read_as() { # read_as <harness> <fixture>: steps.json in a clean folder, then a value from it
	rm -rf "$TMP/read" && mkdir "$TMP/read"
	case "$2" in
	*.jsonl) (cd "$TMP/read" && python3 "$ROOT/agent/trace.py" read "$1" --final final.txt <"$HERE/fixtures/$2") ;;
	*) (cd "$TMP/read" && python3 "$ROOT/agent/trace.py" read "$1" --record "$HERE/fixtures/$2" --ended 1795432711) ;;
	esac
	read_rc=$?
}
steps() { trace "$TMP/read/steps.json" "$1"; }
SHAPE='[(x["kind"], x.get("tool"), x.get("file")) for x in t["steps"]]'
SUMS='t["totals"]["model_requests"], t["totals"]["tool_calls"], t["totals"]["tokens_in"], t["totals"]["tokens_out"], t["model"]'
read_as claude claude-ok.jsonl
check "Claude Code: requests, tools, tokens and times of a session" '[ "$read_rc" = 0 ] && [ "$(steps "$SUMS")" = "4 3 208102 3797 claude-opus-5-5" ] && [ "$(steps "[round((x[\"end\"] - x[\"start\"]) * 1000) for x in t[\"steps\"]]")" = "[5262, 449, 3293, 404, 28774, 57, 3465]" ]'
check "Claude Code: its last words go to the message file, never to the trace" 'grep -q "sales quota" "$TMP/read/final.txt" && ! grep -q "SECRET\|quota\|somewhere" "$TMP/read/steps.json"'
read_as claude claude-limit.jsonl
check "Claude Code: a plan limit is an error with cause usage_limit" '[ "$read_rc" = 3 ] && [ "$(steps "t[\"error\"], t[\"is_error\"]")" = "usage_limit True" ]'
read_as devin devin-atif.json
check "Devin: requests, tools and tokens of an exported session" '[ "$(steps "$SUMS")" = "3 3 25202 1183 swe-2-high" ] && [ "$(steps "$SHAPE")" = "[('"'"'model'"'"', None, None), ('"'"'tool'"'"', '"'"'write'"'"', '"'"'outside'"'"'), ('"'"'tool'"'"', '"'"'read'"'"', '"'"'in.json'"'"'), ('"'"'model'"'"', None, None), ('"'"'tool'"'"', '"'"'write'"'"', '"'"'out.json'"'"'), ('"'"'model'"'"', None, None)]" ] && ! grep -q SECRET "$TMP/read/steps.json"'
check "Devin: a refused tool call is counted, and the last request ends when the tool exited" '[ "$(steps "t[\"denied\"], [x[\"ok\"] for x in t[\"steps\"] if x[\"kind\"] == \"tool\"], [round(x[\"end\"] - x[\"start\"], 1) for x in t[\"steps\"] if x[\"kind\"] == \"model\"], t[\"loaded\"]")" = "1 [False, True, True] [6.3, 2.3, 2.5] {'"'"'tools'"'"': 11}" ]'
read_as codex codex.jsonl
check "Codex: a turn with its tokens and a tool call" '[ "$(steps "t[\"totals\"][\"turns\"], t[\"totals\"][\"tool_calls\"], t[\"totals\"][\"tokens_in\"], t[\"totals\"][\"cache_read\"]")" = "1 1 24763 24448" ] && ! grep -q SECRET "$TMP/read/steps.json"'
read_as gemini gemini.jsonl
check "Gemini CLI: a tool call and the session tokens" '[ "$(steps "t[\"totals\"][\"tool_calls\"], t[\"totals\"][\"tokens_in\"], t[\"model\"]")" = "1 9000 gemini-3-pro" ] && ! grep -q SECRET "$TMP/read/steps.json"'
read_as opencode opencode.jsonl
check "opencode: a model step and a tool call" '[ "$(steps "t[\"totals\"][\"model_requests\"], t[\"totals\"][\"tool_calls\"], t[\"totals\"][\"tokens_in\"]")" = "1 1 19153" ] && ! grep -q SECRET "$TMP/read/steps.json"'
rm -rf "$TMP/read" && mkdir "$TMP/read"
(cd "$TMP/read" && printf 'plain words, not JSON\n{"type":"assistant","message":"not the shape expected"}\n' | python3 "$ROOT/agent/trace.py" read claude --final final.txt)
check "a record that cannot be read costs the trace, not the session" '[ "$?" = 0 ] && grep -q "plain words" "$TMP/read/final.txt"'
(cd "$TMP/read" && echo '{"a": "SECRET name with spaces"}' | python3 "$ROOT/agent/trace.py" read nosuch --final final.txt)
check "an unknown harness has no record and no error" '[ "$?" = 0 ]'
cat >"$TMP/devin-tool" <<'EOF2'
#!/usr/bin/env bash
while [ $# -gt 0 ]; do [ "$1" = --config ] && cfg="$2"; shift; done
cp "$cfg" "$FAKE_SEEN"
echo '{"devin": {"org_id": "written-by-the-tool"}}' >"$cfg"
echo done
EOF2
chmod +x "$TMP/devin-tool"
cp "$ROOT/agent/harnesses/devin.config.json" "$TMP/devin-config-before"
rm -rf "$TMP/read" && mkdir "$TMP/read" && echo prompt >"$TMP/read/prompt.md"
(cd "$TMP/read" && FAKE_SEEN="$TMP/devin-config-seen" JAP_DEVIN_BIN="$TMP/devin-tool" JAP_AGENT_TRACE=0 "$ROOT/agent/harnesses/devin.sh" prompt.md >/dev/null)
check "Devin: the tool gets a copy of JAP's settings, and what it writes there never reaches the file in git" '[ "$?" = 0 ] && cmp -s "$TMP/devin-config-seen" "$TMP/devin-config-before" && cmp -s "$ROOT/agent/harnesses/devin.config.json" "$TMP/devin-config-before"'
check "Devin: the settings in git hold nothing the tool wrote (no organisation)" 'python3 -c "import json, sys; sys.exit(bool(set(json.load(open(sys.argv[1]))) & {\"devin\", \"shell\", \"theme_mode\"}))" "$ROOT/agent/harnesses/devin.config.json"'
# The backend turns a run's trace file into spans (pipeline/agent_run_trace.py) and is
# tested in its container, which cannot run the runner. Its test reads a copy of two trace
# files made here, a reader run and a writer run; this check fails when the runner writes a
# file of another shape than that copy, so the two ends cannot drift apart unseen.
# JAP_WRITE_FIXTURE=1 make test-agent writes the copy again.
same_shape() {
	python3 - "$ROOT/backend/tests/traces/agent_runs.json" "$TMP"/quota/agent/traces/*.json \
		"$TMP"/reasons/agent/traces/*.json <<'PY'
import json, os, sys

def shape(x, at=""):
    """Every path of keys in it; a list counts once, whatever its length."""
    if isinstance(x, dict):
        return {at}.union(*(shape(v, f"{at}.{k}") for k, v in x.items()))
    if isinstance(x, list):
        return {f"{at}[]"}.union(*(shape(v, f"{at}[]") for v in x))
    return {at}

copy, made = sys.argv[1], [json.load(open(f)) for f in sys.argv[2:]]
if os.environ.get("JAP_WRITE_FIXTURE") == "1":
    json.dump(made, open(copy, "w"), indent=1)
    print(f"wrote {copy}")
kept = json.load(open(copy))
diff = sorted(shape(kept) ^ shape(made))
print("\n".join(diff[:20]), end="")
sys.exit(bool(diff))
PY
}
check "the backend's copy of a run trace has the shape the runner writes today" same_shape

# O24: the codes are written in four places (the two prompts, the runner, the sender's
# allow-list in the backend). A code offered by a prompt and dropped by the runner, or kept
# by the runner and dropped by the sender, would be lost without a word.
codes_agree() {
	python3 - "$ROOT" <<'PY'
import ast, importlib.util, re, sys

root = sys.argv[1]
spec = importlib.util.spec_from_file_location("runner_trace", f"{root}/agent/trace.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
sender = {}
for node in ast.parse(open(f"{root}/backend/observability/trace.py").read()).body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        if node.targets[0].id in ("WHY", "FACTS", "CHECKED", "CAUSES", "FILES", "ROLES"):
            sender[node.targets[0].id] = ast.literal_eval(node.value)
problems = []
def same(what, a, b):
    if a != b:
        problems.append(f"{what}: {sorted(set(a) ^ set(b))}")
same("why, runner and sender", runner.WHY, sender["WHY"])
same("facts, runner and sender", runner.FACTS, sender["FACTS"])
same("outcomes, runner and sender", runner.OUTCOMES, sender["CHECKED"])
same("causes", set(runner.CAUSES) | {"no_answer"}, set(sender["CAUSES"]))
same("files", set(runner.FILES) | {"text", "other", "outside"}, set(sender["FILES"]))
offered = {"outcome": set(), "why": set(), "facts": set()}
not_codes = {"answer_bank", "resume", "max_chars"}
for prompt in ("writer", "checker"):
    text = open(f"{root}/agent/jobs/{prompt}.md").read()
    bullets = re.findall(r"^- `(outcome|why|facts)`(.*?)(?=\n- `|\n\n|\Z)", text, re.S | re.M)
    if {part for part, _ in bullets} != set(offered):
        problems.append(f"{prompt}.md: no outcome, why and facts lists found")
    for part, said in bullets:
        offered[part] |= set(re.findall(r"`([a-z_]+)`", said)) - not_codes
offered["facts"].discard("fact_not_in_bank")  # named there as the reason the list goes with
same("outcomes, prompts and runner", offered["outcome"], set(runner.OUTCOMES))
same("why, prompts and runner", offered["why"], set(runner.WHY))
same("facts, prompts and runner", offered["facts"], set(runner.FACTS))
print("\n".join(problems), end="")
sys.exit(bool(problems))
PY
}
check "the reason codes are the same in the prompts, the runner and the sender's allow-list" codes_agree
check "python3 compiles trace.py" 'python3 -m py_compile "$ROOT/agent/trace.py"'

for f in "$ROOT"/agent/*.sh "$ROOT"/agent/harnesses/*.sh "$ROOT"/agent/tests/*.sh; do
	check "bash -n $(basename "$f")" 'bash -n "$f"'
done
for f in "$ROOT"/agent/contract/*.json; do
	check "valid JSON $(basename "$f")" 'python3 -m json.tool "$f" >/dev/null'
done

[ "$fails" = 0 ] && echo "agent runner: all checks passed" || echo "agent runner: $fails failed"
[ "$fails" = 0 ]
