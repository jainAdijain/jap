import httpx
import pytest

from database.session import ping_database
from llm.ollama import ping_ollama


async def test_ollama_unreachable() -> None:
    check = await ping_ollama("http://127.0.0.1:9", "nomic-embed-text")
    assert check.ok is False
    assert "Not reachable" in check.detail


async def test_ollama_model_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3:latest"}]})

    real = httpx.AsyncClient

    def fake_client(**kwargs: object) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    check = await ping_ollama("http://ollama", "nomic-embed-text")
    assert check.ok is False
    assert "not pulled" in check.detail


async def test_ollama_model_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "nomic-embed-text:latest"}]})

    real = httpx.AsyncClient

    def fake_client(**kwargs: object) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    check = await ping_ollama("http://ollama", "nomic-embed-text")
    assert check.ok is True


async def test_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from config import get_settings
    from database import session

    session.get_engine.cache_clear()
    monkeypatch.setattr(
        get_settings(), "database_url", "postgresql+psycopg://x:x@127.0.0.1:9/x?connect_timeout=1"
    )
    try:
        check = await ping_database()
    finally:
        session.get_engine.cache_clear()
    assert check.ok is False
