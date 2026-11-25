#!/usr/bin/env bash
# A stand-in for `make ENV=... <target>` in agent/tests/run_test.sh (PRD 1.30): JAP's
# reader and writer commands over a small JSON state file (FAKE_STATE), no Docker, no database.
# agent-trace-send (PRD 1.34, O22) notes each run trace it is handed in FAKE_STATE.sent;
#   FAKE_TRACE=down  the trace service refuses: "not sent (why)", exit 1, as JAP says it
#   FAKE_TRACE=off   no trace service is set
#   FAKE_TRACE=hang  JAP never answers
#   FAKE_LONG=1      descriptions and the resume as long as real ones, on one line (AH10)
exec python3 - "$@" <<'PY'
import fcntl, json, os, sys, time
state_file = os.environ["FAKE_STATE"]
# The runner's shards call this in parallel: one at a time, as JAP's database would be.
lock = open(state_file + ".lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)
state = json.load(open(state_file))
target, args = sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:])

def save():
    json.dump(state, open(state_file, "w"))

LONG = os.environ.get("FAKE_LONG") == "1"
def text(what):
    """A posting's text, or one of 9,000 characters ending in a word only the end has."""
    if not LONG:
        return f"SECRET-{what}-TEXT with a sales quota"
    return ("SECRET " + what + " words " * 1500)[:8990] + " LAST-WORD"

def in_shard(pid, shard):
    i, n = map(int, shard.split("/"))
    return int(pid) % n == i - 1

if target == "reader-status":
    print(f"run_active=0 unread={len(state['unread'])} pairs={len(state['pairs'])}")
elif target == "reader-export":
    shard, limit = args.get("SHARD", "1/1"), int(args.get("LIMIT", 50))
    mine = [p for p in state["unread"] if in_shard(p, shard)][:limit]
    pairs = state["pairs"][:limit] if shard.startswith("1/") else []
    print(json.dumps({"contract_version": 1, "postings": [
        {"posting_id": p, "description_hash": "h" + p, "keys": ["skills"],
         "description_text": text("POSTING")} for p in mine],
        "pairs": [{"pair_id": p} for p in pairs]}))
elif target == "reader-import":
    given = json.load(open(args["FILE"]))
    saved = 0
    for key, value in given.items():
        if key.startswith("pair:"):
            state["pairs"].remove(key[5:]); saved += 1
            print(f"{key}: kept apart")
        elif key in state["unread"] and value.get("description_hash") == "h" + key:
            state["unread"].remove(key); saved += 1
            print(f"{key}: saved")
    save()
    print(f"saved {saved} of {len(given)}")
elif target == "writer-status":
    print(f"jobs={len(state['jobs'])} resumes=0")
elif target == "writer-export":
    print(json.dumps({"contract_version": 1, "resumes": [], "resume": text("RESUME"), "jobs": [
        {"application_id": a, "questions": [{"key": "why", "label": "SECRET-QUESTION-TEXT"}]}
        for a in state["jobs"]]}))
elif target == "writer-import":
    given = json.load(open(args["FILE"]))
    for key in given:
        state["jobs"].remove(key)
        print(f"{key}: ready_for_review")
    save()
elif target == "agent-trace-send":
    how = os.environ.get("FAKE_TRACE", "")
    if how == "down":
        print("not sent (RuntimeError: the trace service answered 401)")
        sys.exit(1)
    if how == "hang":
        time.sleep(300)
    if how == "off":
        print("off")
    else:
        run = json.load(open(args["FILE"]))
        with open(state_file + ".sent", "a") as f:
            f.write(os.path.basename(args["FILE"]) + "\n")
        print(f"sent {1 + len(run['sessions'])} spans")
else:
    sys.exit(f"fake-jap: no target {target}")
PY
