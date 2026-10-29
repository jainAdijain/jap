"""Shared fixtures. Database tests run against a separate <db>_test database.

The test database is created and migrated once per session from DATABASE_URL (the dev
database itself is never touched). Without a reachable Postgres those tests are skipped,
unless JAP_REQUIRE_DB=1 (CI), where they fail instead.
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from config import get_settings
from database import session as db_session

BACKEND = Path(__file__).resolve().parent.parent
TABLES = (
    "llm_call, possible_duplicate, dedup_override, skill_alias, company_alias, answer_bank, "
    "application, run, posting, listing, watched_board, company, resume_version, profile"
)


def _test_url() -> str:
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    test_url = _test_url()
    name = make_url(test_url).database
    admin = make_url(test_url).set(database="postgres").render_as_string(hide_password=False)
    try:
        with psycopg.connect(_libpq(admin), autocommit=True, connect_timeout=3) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as exc:
        if os.environ.get("JAP_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Postgres not reachable: {exc}")
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url
    get_settings.cache_clear()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "database" / "migrations"))
    command.upgrade(cfg, "head")
    yield test_url
    if old is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old
    get_settings.cache_clear()


@pytest.fixture
def db(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A clean database and data/ folder for one test."""
    with psycopg.connect(_libpq(database_url), autocommit=True) as conn:
        conn.execute(f"TRUNCATE {TABLES} CASCADE")
    monkeypatch.setenv("JAP_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    yield database_url
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def sql(db: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(_libpq(db), autocommit=True) as conn:
        yield conn


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Inngest events the API sent, instead of sending them."""
    sent: list[tuple[str, dict[str, Any]]] = []

    async def fake_send(name: str, data: dict[str, Any] | None = None) -> None:
        sent.append((name, data or {}))

    import functions.events

    monkeypatch.setattr(functions.events, "send", fake_send)
    for mod in ("api.profile", "api.resumes"):
        monkeypatch.setattr(f"{mod}.send", fake_send)
    return sent


@pytest.fixture
async def api(db: str, events: list[Any]) -> AsyncIterator[httpx.AsyncClient]:
    from main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    await db_session.get_engine().dispose()
