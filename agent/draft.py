#!/usr/bin/env python3
"""Two small decisions the runner makes about a writer's draft (PRD 1.37, W8, W13).

    python3 agent/draft.py needs-check <export.json> <draft.json>
        exit 0 when the draft needs the checker session: it holds a free-text answer, a cover
        letter, a resume read, or anything this cannot place; exit 1 when every answer is to
        a question with options, which JAP's import checks itself (the answer must be one of
        them). Prints why, in a few words.

    python3 agent/draft.py handed <export.json> <out.json>
        adds "_handed_out": [the application ids of the export] to out.json, so JAP notes
        the jobs the answers leave out and does not hand them out again unchanged (W13).

The checker re-reads the whole export, the resume and every job description to check every
claim. On 24 Nov 2026 it took 138,570 tokens to check two jobs whose answers were all choices
from the answer bank, and changed nothing. A choice is still checked by JAP on import, and a
job the writer held stays held. JAP_CHECKER_WHEN=always runs the checker on every draft.
"""

import json
import sys


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def needs_check(export, draft):
    if not isinstance(draft, dict):
        return True, "the draft is not a JSON object"
    asked = {
        str(j.get("application_id")): {str(q.get("key")): q for q in j.get("questions") or []}
        for j in export.get("jobs") or []
        if isinstance(j, dict)
    }
    for app_id, answers in draft.items():
        if app_id.startswith("resume:"):
            return True, "a resume read"
        if app_id not in asked or not isinstance(answers, dict):
            return True, "an answer JAP did not ask for"
        for key in answers:
            if key == "_review":
                continue
            q = asked[app_id].get(key)
            if q is None:
                return True, "an answer JAP did not ask for"
            if q.get("cover_letter"):
                return True, "a cover letter"
            if not q.get("options"):
                return True, "a free-text answer"
    return False, "only answers chosen from options"


def main(argv):
    what, export_file, other = argv[1], argv[2], argv[3]
    export = load(export_file)
    if what == "needs-check":
        needed, why = needs_check(export, load(other))
        print(why)
        return 0 if needed else 1
    if what == "handed":
        out = load(other)
        out["_handed_out"] = [
            str(j["application_id"])
            for j in export.get("jobs") or []
            if isinstance(j, dict) and "application_id" in j
        ]
        with open(other, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        return 0
    print(f"draft.py: no command {what}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except (OSError, ValueError, IndexError, KeyError) as exc:
        print(f"draft.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(0 if len(sys.argv) > 1 and sys.argv[1] == "needs-check" else 1)
