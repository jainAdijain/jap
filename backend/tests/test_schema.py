from typing import Any

import psycopg
import pytest

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
