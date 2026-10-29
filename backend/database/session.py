"""Async engine and session factory for Postgres (psycopg 3)."""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import get_settings
from schemas.health import Check


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    async with get_sessionmaker()() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


async def ping_database() -> Check:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
            ext = await conn.execute(
                text(
                    "SELECT count(*) FROM pg_extension"
                    " WHERE extname IN ('vector', 'pg_trgm', 'pgcrypto')"
                )
            )
            found = ext.scalar_one()
    except Exception as exc:  # noqa: BLE001 - any failure means not reachable
        return Check(ok=False, detail=f"Not reachable: {type(exc).__name__}")
    if found < 3:
        return Check(ok=False, detail=f"Connected, but only {found} of 3 extensions installed")
    return Check(ok=True, detail="Connected, pgvector ready")
