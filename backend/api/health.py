"""GET /api/health: is the API up, and can it reach the database, Inngest and Ollama."""

import asyncio

from fastapi import APIRouter

from config import get_settings
from database.session import ping_database
from functions.client import ping_inngest
from llm.ollama import ping_ollama
from schemas.health import Check, Health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
async def health() -> Health:
    settings = get_settings()
    database, inngest, ollama = await asyncio.gather(
        ping_database(),
        ping_inngest(settings.inngest_base_url),
        ping_ollama(settings.ollama_base_url, settings.jap_model_embed),
    )
    checks = {
        "api": Check(ok=True, detail="Responding"),
        "database": database,
        "inngest": inngest,
        "ollama": ollama,
    }
    return Health(ok=all(c.ok for c in checks.values()), checks=checks)
