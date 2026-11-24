#!/usr/bin/env bash
# A stand-in harness for agent/tests/run_test.sh (PRD 1.30): no model. It answers every
# posting with "unknown" values, every pair with not the same, and every job with "ok",
# so the runner's loop, shards and imports can be tested without a subscription. Like a real
# harness it gives a record of its session (PRD 1.33, O17), in Claude Code's stream shape,
# with text in it that must not reach a trace.
#   FAKE_LIMIT=1       report a usage limit instead
#   FAKE_SIGNED_OUT=1  fail as a harness that is not signed in
#   FAKE_RECORD=0      give no record, as a harness that cannot
#   FAKE_SAY=<words>   end with these words (a session that worked and mentions a quota)
#   FAKE_EDIT_INPUT=1  as the writer, change in.json, which a harness should only read
set -u
. "$(dirname "$0")/common.sh"
out=$(mktemp)
# AH8: a harness must never be handed the trace service's settings; the test looks for this.
[ -z "${JAP_TRACE_ENDPOINT:-}${JAP_TRACE_HEADERS:-}" ] || touch "$FAKE_STATE.saw-trace-key"
if [ "${FAKE_LIMIT:-}" = 1 ]; then
	echo "You've hit your usage limit" >"$out"
	finish 1 "$out"
fi
if [ "${FAKE_SIGNED_OUT:-}" = 1 ]; then
	echo "Not logged in · Please run /login" >"$out"
	finish 1 "$out"
fi
work() {
	python3 - "$1" <<'PY'
import json, os, sys
prompt = open(sys.argv[1]).read()
data = json.load(open("in.json"))
if "JAP reader job" in prompt:
    out = {p["posting_id"]: {"description_hash": p["description_hash"],
                             **{k: "unknown" for k in p["keys"]}}
           for p in data.get("postings", [])}
    out.update({f"pair:{p['pair_id']}": {"same": False, "reason": "fake"}
                for p in data.get("pairs", [])})
    json.dump(out, open("out.json", "w"))
    wrote = "out.json"
elif "JAP writer job" in prompt:
    out = {j["application_id"]: {q["key"]: "ok" for q in j["questions"]} for j in data["jobs"]}
    json.dump(out, open("draft.json", "w"))
    wrote = "draft.json"
    if os.environ.get("FAKE_EDIT_INPUT") == "1":
        json.dump({**data, "edited_by_the_harness": True}, open("in.json", "w"))
else:
    draft = json.load(open("draft.json"))
    json.dump(draft, open("out.json", "w"))
    open("findings.txt", "w").write("".join(f"{k}: ok\n" for k in draft))
    wrote = "out.json"
said = os.environ.get("FAKE_SAY") or "fake harness done"
if os.environ.get("FAKE_RECORD") == "0" or os.environ.get("JAP_AGENT_TRACE") == "0":
    print(said)
    sys.exit(0)
text = open("in.json").read()  # a real record repeats what the harness read
def reply(rid, tokens, block):
    return {"type": "assistant", "request_id": rid, "message": {
        "model": "fake-1", "content": [block],
        "usage": {"input_tokens": tokens, "cache_read_input_tokens": 100, "output_tokens": 7}}}
for line in (
    {"type": "system", "subtype": "init", "model": "fake-1", "claude_code_version": "0.0.1",
     "tools": ["Read", "Write"], "mcp_servers": []},
    reply("r1", 1000, {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "in.json"}}),
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": text}]}},
    reply("r2", 2000, {"type": "tool_use", "id": "t2", "name": "Write",
                       "input": {"file_path": wrote, "content": text}}),
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2"}]}},
    reply("r3", 3000, {"type": "text", "text": text}),
    {"type": "result", "is_error": False, "num_turns": 3, "total_cost_usd": 0.01,
     "permission_denials": [], "result": said},
):
    print(json.dumps(line))
PY
}
if traced && [ "${FAKE_RECORD:-1}" != 0 ]; then
	work "$1" 2>>"$out" | record fake "$out"
	codes=("${PIPESTATUS[@]}")
	rc=${codes[0]}
	[ "$rc" -eq 0 ] && [ "${codes[1]}" -eq 3 ] && rc=1
else
	work "$1" >"$out" 2>&1
	rc=$?
fi
finish "$rc" "$out"
