"""Loads seed/companies.yaml and seed/skills.yaml (make seed, make reset).

Phase 0 only checks that both files parse. The upserts into watched_board and
skill_alias are added with those tables (Phase 1 and Phase 2), and stay safe to repeat.
"""

import os
import sys
from pathlib import Path
from typing import Any

import yaml

# In the app container seed/ is mounted at /seed; elsewhere it sits at the repo root.
SEED_DIR = Path(os.environ.get("JAP_SEED_DIR", Path(__file__).resolve().parents[2] / "seed"))
ATS_KEYS = {"greenhouse", "lever", "ashby"}


def load(name: str) -> Any:
    with (SEED_DIR / name).open() as f:
        return yaml.safe_load(f) or {}


def check_companies(data: Any) -> int:
    if not isinstance(data, dict) or not set(data) <= ATS_KEYS:
        raise ValueError(f"companies.yaml: top-level keys must be among {sorted(ATS_KEYS)}")
    count = 0
    for ats, boards in data.items():
        for board in boards or []:
            if not isinstance(board, dict) or not {"token", "name"} <= set(board):
                raise ValueError(f"companies.yaml: {ats} entry needs token and name: {board!r}")
            count += 1
    return count


def check_skills(data: Any) -> int:
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError("skills.yaml: must map alias -> canonical name")
    return len(data)


def main() -> int:
    boards = check_companies(load("companies.yaml"))
    skills = check_skills(load("skills.yaml"))
    print(f"Seed files OK: {boards} boards, {skills} skill aliases (tables arrive in Phase 1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
