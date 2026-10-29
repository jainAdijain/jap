from typing import Any

import psycopg
import pytest

from database import seed


def test_seed_files_parse() -> None:
    assert seed.check_companies(seed.load("companies.yaml")) >= 0
    assert seed.check_skills(seed.load("skills.yaml")) > 0


def test_companies_rejects_unknown_ats() -> None:
    with pytest.raises(ValueError):
        seed.check_companies({"workday": [{"token": "x", "name": "X"}]})


async def test_upsert_is_safe_to_repeat(db: str, sql: psycopg.Connection[Any]) -> None:
    companies = {"greenhouse": [{"token": "Acme", "name": "Acme"}], "lever": []}
    await seed.upsert(companies, {"k8s": "kubernetes"})
    sql.execute("UPDATE watched_board SET active = false")  # removed in the UI
    await seed.upsert(companies, {"k8s": "Kubernetes", "js": "javascript"})
    boards = sql.execute("SELECT board_token, active FROM watched_board").fetchall()
    assert boards == [("acme", False)]
    aliases = dict(sql.execute("SELECT alias, canonical FROM skill_alias").fetchall())
    assert aliases == {"k8s": "kubernetes", "js": "javascript"}
