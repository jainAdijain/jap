#!/usr/bin/env bash
# The runner (agent/run.sh, PRD 1.30 AH4) against a stand-in JAP (fake-jap.sh) and a stand-in
# harness (harnesses/fake.sh): every posting and pair read across 3 shards and a leftover
# pass, writer answers checked and imported, a usage limit stops the run with exit 75, and an
# unknown harness is refused. Then the trace (agent/trace.py, PRD 1.33 O16 to O21): a trace per
# run and per session with model requests, tool calls and tokens, a cause for each kind of
# failure, no text in any of it, and each harness's record read from a fixture.
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
check "failures.md reaches the batch folder" '[ -f "$TMP"/work/*-reader/reader-*-of-3/batch-1/failures.md ]'
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
case_run edited "$ONE" writer FAKE_EDIT_INPUT=1
edited=$(ls -d "$TMP"/work/*-writer/writer | tail -1)
check "a harness that changes its in.json is named, and the checker gets the export as JAP gave it" '[ "$rc" = 0 ] && grep -q edited_by_the_harness "$edited/in.json" && ! grep -q edited_by_the_harness "$edited/check/in.json" && grep -q "The writer changed its in.json" "$TMP/edited.out" && [ "$(trace "$got" "[(s[\"role\"], s[\"input_changed\"]) for s in t[\"sessions\"]], t[\"totals\"][\"input_changed\"]")" = "[('"'"'writer'"'"', True), ('"'"'checker'"'"', False)] 1" ]'
printf '#!/bin/sh\nexit 0\n' >"$TMP/dead-jap.sh" && chmod +x "$TMP/dead-jap.sh"
case_run noanswer "$ONE" reader JAP_MAKE="$TMP/dead-jap.sh"
check "JAP not answering is a run with no session and a cause" '[ "$rc" = 1 ] && [ "$(trace "$got" "t[\"cause\"], t[\"totals\"][\"sessions\"]")" = "no_answer 0" ]'

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
check "python3 compiles trace.py" 'python3 -m py_compile "$ROOT/agent/trace.py"'

for f in "$ROOT"/agent/*.sh "$ROOT"/agent/harnesses/*.sh "$ROOT"/agent/tests/*.sh; do
	check "bash -n $(basename "$f")" 'bash -n "$f"'
done
for f in "$ROOT"/agent/contract/*.json; do
	check "valid JSON $(basename "$f")" 'python3 -m json.tool "$f" >/dev/null'
done

[ "$fails" = 0 ] && echo "agent runner: all checks passed" || echo "agent runner: $fails failed"
[ "$fails" = 0 ]
