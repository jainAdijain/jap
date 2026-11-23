#!/usr/bin/env bash
# The runner (agent/run.sh, PRD 1.30 AH4) against a stand-in JAP (fake-jap.sh) and a stand-in
# harness (harnesses/fake.sh): every posting and pair read across 3 shards and a leftover
# pass, writer answers checked and imported, a usage limit stops the run with exit 75, and an
# unknown harness is refused. Needs bash and python3 only. Run with make test-agent.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fails=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

export FAKE_STATE="$TMP/state.json" JAP_MAKE="$HERE/fake-jap.sh" JAP_DATA_HOST_DIR="$TMP/data"
export JAP_AGENT_WORK_DIR="$TMP/work" JAP_FAILURES_FILE="$TMP/failures.md"
export JAP_HARNESS=fake JAP_AGENT_SHARDS=3 JAP_AGENT_BATCH=4 JAP_AGENT_TIMEOUT_MIN=1
state() { python3 -c "import json; print(json.dumps(json.load(open('$FAKE_STATE'))$1))"; }

python3 -c "import json; json.dump({'unread': [str(i) for i in range(1, 30)],
  'pairs': ['p1', 'p2'], 'jobs': ['a1', 'a2']}, open('$FAKE_STATE', 'w'))"
"$ROOT/agent/run.sh" reader >"$TMP/reader.out" 2>&1
rc=$?
check "reader exits 0" '[ "$rc" = 0 ]'
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

for f in "$ROOT"/agent/*.sh "$ROOT"/agent/harnesses/*.sh "$ROOT"/agent/tests/*.sh; do
	check "bash -n $(basename "$f")" 'bash -n "$f"'
done
for f in "$ROOT"/agent/contract/*.json; do
	check "valid JSON $(basename "$f")" 'python3 -m json.tool "$f" >/dev/null'
done

[ "$fails" = 0 ] && echo "agent runner: all checks passed" || echo "agent runner: $fails failed"
[ "$fails" = 0 ]
