#!/usr/bin/env python3
"""Long text out of in.json, into wrapped files a harness can read whole (PRD 1.36, AH10).

JAP's export is JSON, and a long string in JSON is one line: a job description of 6,600 to
9,000 characters, the resume, the rules of a prompt. Devin's file reader, its search, and
the overflow files it writes cut a line at 2,000 characters. On 24 Nov 2026 a Devin reader
on live filled postings from the first 2,000 characters, and another searched in.json 108
times for single words to get round it (1.48 million tokens for 5 postings); JAP saved both.

So the runner calls this on every batch folder after the export: each string longer than
LONG characters, wherever it is in in.json, goes to a file of its own under text/, with its
own line breaks and every line wrapped at WIDTH, and its value in in.json becomes
"@text/<file>". The writer and checker prompts (1.4) say to read such a file in full; the
reader gets the files in its prompt (agent/inline.py, AH11).
Nothing else changes: the ids, the description hashes and the keys stay where they were,
and JAP's import never reads in.json. Short values stay in place.

    python3 agent/wrap.py <batch folder>    prints the longest line left, in characters

Without python3 the runner goes on with in.json as JAP gave it.
"""

import json
import os
import re
import sys
import textwrap

LONG = 600
WIDTH = 100
REF = "@text/"


def name_for(path):
    """A file name from where the value was: postings-3-description_text.txt."""
    joined = "-".join(str(p) for p in path) or "value"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", joined)[:120] + ".txt"


def wrapped(text):
    lines = []
    for line in text.splitlines() or [""]:
        lines += textwrap.wrap(line, WIDTH, break_long_words=True, break_on_hyphens=False) or [""]
    return "\n".join(lines) + "\n"


def move(value, path, folder, written):
    if isinstance(value, dict):
        return {k: move(v, (*path, k), folder, written) for k, v in value.items()}
    if isinstance(value, list):
        return [move(v, (*path, i), folder, written) for i, v in enumerate(value)]
    if isinstance(value, str) and len(value) > LONG and not value.startswith(REF):
        name = name_for(path)
        with open(os.path.join(folder, "text", name), "w", encoding="utf-8") as f:
            f.write(wrapped(value))
        written.append(name)
        return REF + name
    return value


def longest(folder):
    """The longest line of in.json and of every file under text/, in characters."""
    most = 0
    for path in [os.path.join(folder, "in.json")] + [
        os.path.join(folder, "text", n) for n in sorted(os.listdir(os.path.join(folder, "text")))
    ]:
        with open(path, encoding="utf-8", errors="replace") as f:
            most = max([most, *(len(line.rstrip("\n")) for line in f)])
    return most


def main(folder):
    source = os.path.join(folder, "in.json")
    with open(source, encoding="utf-8") as f:
        data = json.load(f)
    os.makedirs(os.path.join(folder, "text"), exist_ok=True)
    written = []
    data = move(data, (), folder, written)
    tmp = source + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, source)
    print(longest(folder))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1]))
    except (OSError, ValueError, IndexError) as exc:
        print(f"wrap: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
