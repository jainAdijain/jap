"""Loads seed/companies.yaml and seed/skills.yaml (make seed, make reset).

Upserts, so it is safe to repeat: boards you removed in the UI stay removed, and
aliases are refreshed from the file.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.dialects.postgresql import insert

from database.models import SkillAlias, WatchedBoard
from database.session import get_sessionmaker

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


async def upsert(companies: Any, skills: dict[str, str]) -> None:
    async with get_sessionmaker()() as session:
        for ats, boards in companies.items():
            for board in boards or []:
                await session.execute(
                    insert(WatchedBoard)
                    .values(
                        ats=ats,
                        board_token=str(board["token"]).lower(),
                        display_name=board["name"],
                        origin="seed",
                    )
                    .on_conflict_do_nothing(index_elements=["ats", "board_token"])
                )
        for alias, canonical in skills.items():
            stmt = insert(SkillAlias).values(alias=alias.lower(), canonical=canonical.lower())
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["alias"], set_={"canonical": stmt.excluded.canonical}
                )
            )
        await session.commit()


def main() -> int:
    companies = load("companies.yaml")
    skills = load("skills.yaml")
    boards = check_companies(companies)
    aliases = check_skills(skills)
    asyncio.run(upsert(companies, skills))
    print(f"Seeded {boards} boards and {aliases} skill aliases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
