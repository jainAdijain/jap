import pytest
from fastapi.testclient import TestClient

import api.health as health_module
from main import app
from schemas.health import Check


def _ok(detail: str) -> Check:
    return Check(ok=True, detail=detail)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def db() -> Check:
        return _ok("db")

    async def inngest(_: str) -> Check:
        return _ok("inngest")

    async def ollama(_: str, __: str) -> Check:
        return _ok("ollama")

    monkeypatch.setattr(health_module, "ping_database", db)
    monkeypatch.setattr(health_module, "ping_inngest", inngest)
    monkeypatch.setattr(health_module, "ping_ollama", ollama)
    return TestClient(app)


def test_health_all_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert set(body["checks"]) == {"api", "database", "inngest", "ollama"}


def test_health_reports_ollama_down(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def ollama_down(_: str, __: str) -> Check:
        return Check(ok=False, detail="Not reachable: ConnectError")

    monkeypatch.setattr(health_module, "ping_ollama", ollama_down)
    body = client.get("/api/health").json()
    assert body["ok"] is False
    assert body["checks"]["ollama"]["ok"] is False
    assert body["checks"]["database"]["ok"] is True


def test_cors_allows_only_ui_origin(client: TestClient) -> None:
    allowed = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    other = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_inngest_endpoint_lists_functions(client: TestClient) -> None:
    body = client.get("/api/inngest").json()
    assert body["function_count"] >= 1
