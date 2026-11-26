#!/usr/bin/env python3
"""The batch in the prompt, the answer in the reply (PRD 1.41, AH11).

A harness sends everything so far again with each model request. A reader session that
opened in.json and then each description file made a request per file: on 26 Nov 2026 a
batch of 9 postings took 14 requests, growing from 8,000 to 34,000 tokens, 282,000 in all
for about 20,000 tokens of job text. With the batch in the prompt a session needs one
request, and the reply is the answer itself, so there is no write and no closing turn.

    python3 agent/inline.py prompt <batch folder>   adds the batch to <folder>/prompt.md:
                                                    in.json, then each text/ file it names
    python3 agent/inline.py reply <batch folder>    the JSON object in the harness's last
                                                    words (harness.log) to out.json, unless
                                                    the session wrote out.json itself

Both print nothing. reply exits 1 when there is no JSON object to take, which the runner
counts as a session that wrote nothing. The batch folder keeps in.json and text/ as they
were, and JAP's import never reads the prompt.
"""

import json
import os
import re
import sys

REF = re.compile(r'"@text/([A-Za-z0-9_.-]+)"')


def prompt(folder):
    raw = open(os.path.join(folder, "in.json"), encoding="utf-8").read()
    parts = ["", "---", "", "# The batch", "", "## in.json", "", raw.strip(), ""]
    seen = []
    for name in REF.findall(raw):
        if name in seen:
            continue
        seen.append(name)
        text = open(os.path.join(folder, "text", name), encoding="utf-8").read()
        parts += [f"## text/{name}", "", text.rstrip(), "", f"(end of text/{name})", ""]
    with open(os.path.join(folder, "prompt.md"), "a", encoding="utf-8") as f:
        f.write("\n".join(parts))


def answer(said):
    """The largest JSON object in what the harness said, or None."""
    decoder = json.JSONDecoder()
    best = None
    for m in re.finditer(r"\{", said):
        try:
            obj, end = decoder.raw_decode(said, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and (best is None or end - m.start() > best[0]):
            best = (end - m.start(), obj)
    return None if best is None else best[1]


def reply(folder):
    out = os.path.join(folder, "out.json")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return 0
    try:
        said = open(os.path.join(folder, "harness.log"), encoding="utf-8").read()
    except OSError:
        return 1
    obj = answer(said)
    if not obj:
        return 1
    with open(out, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("prompt", "reply"):
        sys.exit("usage: inline.py prompt|reply <batch folder>")
    if sys.argv[1] == "prompt":
        prompt(sys.argv[2])
    else:
        sys.exit(reply(sys.argv[2]))
