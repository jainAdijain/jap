import importlib.util
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parent.parent
EXPECTED = {
    "profile",
    "resume_version",
    "company",
    "listing",
    "posting",
    "application",
    "answer_bank",
    "run",
    "watched_board",
    "company_alias",
    "skill_alias",
    "dedup_override",
    "possible_duplicate",
    "llm_call",
}


def test_all_tables_exist(sql: psycopg.Connection[Any]) -> None:
    rows = sql.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert EXPECTED <= {r[0] for r in rows}


def test_profile_needs_a_work_mode(sql: psycopg.Connection[Any]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        sql.execute("INSERT INTO profile (work_modes) VALUES ('{}')")


def test_profile_is_single_row(sql: psycopg.Connection[Any]) -> None:
    sql.execute("INSERT INTO profile DEFAULT VALUES")
    with pytest.raises(psycopg.errors.UniqueViolation):
        sql.execute("INSERT INTO profile DEFAULT VALUES")


def _listing_and_posting(sql: psycopg.Connection[Any]) -> tuple[str, str, str]:
    company = sql.execute(
        "INSERT INTO company (name, canonical_name) VALUES ('Acme', 'acme') RETURNING id"
    ).fetchone()[0]  # type: ignore[index]
    board = sql.execute(
        "INSERT INTO watched_board (ats, board_token, display_name, origin) "
        "VALUES ('greenhouse', 'acme', 'Acme', 'user') RETURNING id"
    ).fetchone()[0]  # type: ignore[index]
    listing = sql.execute(
        "INSERT INTO listing (company_id, canonical_role, title_display) "
        "VALUES (%s, 'backend engineer', 'Backend Engineer') RETURNING id",
        (company,),
    ).fetchone()[0]  # type: ignore[index]
    posting = sql.execute(
        "INSERT INTO posting (source, external_id, watched_board_id, listing_id, company_name_raw,"
        " title_raw, posting_url, apply_url, description_html, description_text,"
        " description_hash, raw_payload) VALUES ('greenhouse', '1', %s, %s, 'Acme', 'BE',"
        " 'https://x', 'https://x', '<p>x</p>', 'x', repeat('a', 64), '{}') RETURNING id",
        (board, listing),
    ).fetchone()[0]  # type: ignore[index]
    resume = sql.execute(
        "INSERT INTO resume_version (file_path, file_sha256, original_name, mime)"
        " VALUES ('/data/r.pdf', repeat('b', 64), 'r.pdf', 'application/pdf') RETURNING id"
    ).fetchone()[0]  # type: ignore[index]
    return str(listing), str(posting), str(resume)


def test_posting_unique_per_board_job(sql: psycopg.Connection[Any]) -> None:
    _listing_and_posting(sql)
    with pytest.raises(psycopg.errors.UniqueViolation):
        sql.execute(
            "INSERT INTO posting (source, external_id, watched_board_id, company_name_raw,"
            " title_raw, posting_url, apply_url, description_html, description_text,"
            " description_hash, raw_payload) SELECT 'greenhouse', '1', watched_board_id, 'A',"
            " 'B', 'u', 'u', 'h', 't', repeat('a', 64), '{}' FROM posting LIMIT 1"
        )


def test_postings_are_never_deleted(sql: psycopg.Connection[Any]) -> None:
    _listing_and_posting(sql)
    with pytest.raises(psycopg.errors.RaiseException):
        sql.execute("DELETE FROM posting")


def test_one_applied_per_listing_and_applied_is_frozen(sql: psycopg.Connection[Any]) -> None:
    listing, posting, resume = _listing_and_posting(sql)
    insert = (
        "INSERT INTO application (listing_id, posting_id, resume_version_id, mode, status)"
        " VALUES (%s, %s, %s, 'review', 'applied') RETURNING id"
    )
    app_id = sql.execute(insert, (listing, posting, resume)).fetchone()[0]  # type: ignore[index]
    with pytest.raises(psycopg.errors.UniqueViolation):
        sql.execute(insert, (listing, posting, resume))
    sql.execute("UPDATE application SET email_sent_at = now() WHERE id = %s", (app_id,))
    with pytest.raises(psycopg.errors.RaiseException):
        sql.execute("UPDATE application SET answers = '{}' WHERE id = %s", (app_id,))
    with pytest.raises(psycopg.errors.RaiseException):
        sql.execute("DELETE FROM application WHERE id = %s", (app_id,))


def test_one_queued_run(sql: psycopg.Connection[Any]) -> None:
    sql.execute("INSERT INTO run (trigger, mode) VALUES ('manual', 'find_only')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        sql.execute("INSERT INTO run (trigger, mode) VALUES ('scheduled', 'find_only')")


def test_updated_at_moves(sql: psycopg.Connection[Any]) -> None:
    sql.execute("INSERT INTO skill_alias VALUES ('k8s', 'kubernetes')")
    before = sql.execute("SELECT updated_at FROM skill_alias").fetchone()[0]  # type: ignore[index]
    sql.execute("UPDATE skill_alias SET canonical = 'kube'")
    after = sql.execute("SELECT updated_at FROM skill_alias").fetchone()[0]  # type: ignore[index]
    assert after >= before


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('[["role"], ["skills"], ["location"]]', [["role"], ["location"]]),  # the old default
        ('[["location", "role", "skills"]]', [["location", "role"]]),  # the old third choice
        ('[["role", "location"], ["skills"]]', [["location", "role"]]),
        ('[["skills"]]', [["location", "role"]]),  # skills alone: title and location
        ('[["skills", "role"], ["role"]]', [["role"]]),  # repeats left by dropping skills
        ('[["location", "role"]]', [["location", "role"]]),  # untouched
        ('[["location"]]', [["location"]]),
    ],
)
def test_migration_drops_skills_from_saved_rules(
    sql: psycopg.Connection[Any], before: str, after: list[list[str]]
) -> None:
    """0010: a rule saved before PRD 1.21 keeps its other conditions (F5)."""
    path = Path(__file__).parent.parent / "database/migrations/versions/0010_skills_match.py"
    spec = importlib.util.spec_from_file_location("m0010", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sql.execute("INSERT INTO profile (match_rule) VALUES (%s::jsonb)", (before,))
    sql.execute(module.CLEAN_RULES)
    row = sql.execute("SELECT match_rule FROM profile").fetchone()
    assert row is not None and row[0] == after


def test_skills_match_columns(sql: psycopg.Connection[Any]) -> None:
    """0010: the stored skills match and the auto-apply bar, with their defaults and bounds."""
    sql.execute("INSERT INTO profile DEFAULT VALUES")
    row = sql.execute("SELECT auto_apply_min_skills, match_rule FROM profile").fetchone()
    assert row == (60, [["location", "role"]])
    listing, _, _ = _listing_and_posting(sql)
    row = sql.execute(
        "SELECT required_hit, nice_hit, required_pct FROM listing WHERE id = %s", (listing,)
    ).fetchone()
    assert row == ([], [], None)  # unknown until the first refilter, never 0
    with pytest.raises(psycopg.errors.CheckViolation):
        sql.execute("UPDATE listing SET required_pct = 101")
    with pytest.raises(psycopg.errors.CheckViolation):
        sql.execute("UPDATE profile SET auto_apply_min_skills = -1")


def test_migration_lists_jobs_the_skills_condition_hid(
    sql: psycopg.Connection[Any], database_url: str
) -> None:
    """0010 applied to a database from before PRD 1.21: a profile on the old third choice
    (title, location and every required skill) sees the jobs that only skills kept out, each
    with its skills match, without waiting for a search or a save."""
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "database" / "migrations"))
    command.downgrade(cfg, "0009_close_reason")
    try:
        sql.execute(
            "INSERT INTO profile (target_roles, must_have_skills, nice_to_have_skills, locations,"
            " match_rule, work_modes) VALUES ('{Backend Engineer}', '{python,rust}', '{docker}',"
            """ '{Bangalore}', '[["location", "role", "skills"]]', '{onsite,hybrid}')"""
        )
        company = sql.execute(
            "INSERT INTO company (name, canonical_name) VALUES ('Acme', 'acme') RETURNING id"
        ).fetchone()
        assert company is not None
        for title, skills, mode, matched in (
            ("Backend Engineer", "{python,rust}", "onsite", "{role,skills,location}"),
            ("Senior Backend Engineer", "{python,docker}", "onsite", None),  # hidden: no rust
            ("Staff Backend Engineer", "{}", "hybrid", None),  # hidden: no skills extracted
            ("Remote Backend Engineer", "{python,rust}", "remote", None),  # work mode not mine
            ("Data Analyst", "{python,rust}", "onsite", None),  # skills alone never list a job
        ):
            sql.execute(
                "INSERT INTO listing (company_id, canonical_role, title_display, locations,"
                " skills, work_mode, matched) VALUES (%s, %s, %s, '{\"Bangalore, India\"}',"
                " %s, %s, %s)",
                (company[0], title.lower(), title, skills, mode, matched),
            )
    finally:
        command.upgrade(cfg, "head")
    assert sql.execute("SELECT match_rule FROM profile").fetchone() == ([["location", "role"]],)
    rows = sql.execute(
        "SELECT title_display, matched, required_hit, nice_hit, required_pct FROM listing"
        " ORDER BY title_display"
    ).fetchall()
    assert rows == [
        ("Backend Engineer", ["role", "location"], ["python", "rust"], [], 100),
        ("Data Analyst", None, ["python", "rust"], [], 100),  # title is not one of mine
        # Work mode is still a filter: not listed, though its skills match is worked out.
        ("Remote Backend Engineer", None, ["python", "rust"], [], 100),
        ("Senior Backend Engineer", ["role", "location"], ["python"], ["docker"], 50),
        ("Staff Backend Engineer", ["role", "location"], [], [], None),  # Skills not stated
    ]
