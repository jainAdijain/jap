#!/usr/bin/env bash
# A stand-in harness for agent/tests/run_test.sh (PRD 1.30): no model. It answers every
# posting with "unknown" values, every pair with not the same, and every job with "ok",
# so the runner's loop, shards and imports can be tested without a subscription.
# FAKE_LIMIT=1 makes it report a usage limit instead.
set -u
. "$(dirname "$0")/common.sh"
out=$(mktemp)
if [ "${FAKE_LIMIT:-}" = 1 ]; then
	echo "You've hit your usage limit" >"$out"
	finish 1 "$out"
fi
python3 - "$1" >"$out" 2>&1 <<'PY'
import json, sys
prompt = open(sys.argv[1]).read()
data = json.load(open("in.json"))
if "JAP reader job" in prompt:
    out = {p["posting_id"]: {"description_hash": p["description_hash"],
                             **{k: "unknown" for k in p["keys"]}}
           for p in data.get("postings", [])}
    out.update({f"pair:{p['pair_id']}": {"same": False, "reason": "fake"}
                for p in data.get("pairs", [])})
    json.dump(out, open("out.json", "w"))
elif "JAP writer job" in prompt:
    out = {j["application_id"]: {q["key"]: "ok" for q in j["questions"]} for j in data["jobs"]}
    json.dump(out, open("draft.json", "w"))
else:
    draft = json.load(open("draft.json"))
    json.dump(draft, open("out.json", "w"))
    open("findings.txt", "w").write("".join(f"{k}: ok\n" for k in draft))
print("fake harness done")
PY
finish $? "$out"
