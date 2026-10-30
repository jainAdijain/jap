"""Microsoft 365 sender (E4): token renewal on send, the sendMail body, and EMAIL_PROVIDER."""

import base64
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from config import get_settings
from pipeline import microsoft, notify
from pipeline.notify import EmailNotSent, Mail


@pytest.fixture(autouse=True)
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMAIL_PROVIDER", "microsoft")
    monkeypatch.setenv("MS_CLIENT_ID", "client-1")
    monkeypatch.setenv("MS_TENANT_ID", "tenant-1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _token(expires_at: float) -> None:
    microsoft.token_path().write_text(
        json.dumps({"access_token": "old", "refresh_token": "r1", "expires_at": int(expires_at)})
    )


class Graph:
    """A stand-in for login.microsoftonline.com and graph.microsoft.com."""

    def __init__(self, token_status: int = 200, send_status: int = 202) -> None:
        self.token_status = token_status
        self.send_status = send_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/tenant-1/oauth2/v2.0/token":
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(
                200, json={"access_token": "new", "refresh_token": "r2", "expires_in": 3600}
            )
        if request.url.path == "/v1.0/me/sendMail":
            return httpx.Response(self.send_status)
        return httpx.Response(404)


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> Graph:
    fake = Graph()
    real = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(fake), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return fake


MAIL = Mail(to="me@example.com", subject="Applied: Engineer at Acme", body="You applied.\n")


async def test_valid_token_is_used_without_renewing(graph: Graph) -> None:
    _token(time.time() + 3000)
    await notify.send(MAIL)
    assert [r.url.path for r in graph.requests] == ["/v1.0/me/sendMail"]
    assert graph.requests[0].headers["Authorization"] == "Bearer old"


async def test_expired_token_is_renewed_and_saved_before_sending(graph: Graph) -> None:
    _token(time.time() + 60)  # inside the early window counts as expired
    await notify.send(MAIL)
    renew, send = graph.requests
    assert b"grant_type=refresh_token" in renew.content and b"refresh_token=r1" in renew.content
    assert send.headers["Authorization"] == "Bearer new"
    saved = json.loads(microsoft.token_path().read_text())
    assert saved["refresh_token"] == "r2"
    assert saved["expires_at"] > time.time() + 3000
    assert microsoft.token_path().stat().st_mode & 0o777 == 0o600


async def test_revoked_refresh_token_asks_to_sign_in_again(graph: Graph) -> None:
    graph.token_status = 400
    _token(time.time() - 10)
    with pytest.raises(EmailNotSent, match="make email-login"):
        await notify.send(MAIL)


async def test_not_signed_in(graph: Graph) -> None:
    with pytest.raises(EmailNotSent, match="make email-login"):
        await notify.send(MAIL)
    assert graph.requests == []


async def test_graph_refusal_is_email_not_sent(graph: Graph) -> None:
    graph.send_status = 403
    _token(time.time() + 3000)
    with pytest.raises(EmailNotSent, match="403"):
        await notify.send(MAIL)


def test_message_body_with_attachment(tmp_path: Path) -> None:
    pdf = tmp_path / "letter.pdf"
    pdf.write_bytes(b"%PDF-1.4 letter")
    body = microsoft.message(
        Mail(to="me@example.com", subject="S", body="B", attachment=pdf, attachment_name="L.pdf")
    )
    msg = body["message"]
    assert msg["toRecipients"] == [{"emailAddress": {"address": "me@example.com"}}]
    assert msg["body"] == {"contentType": "Text", "content": "B"}
    (att,) = msg["attachments"]
    assert att["name"] == "L.pdf" and att["contentType"] == "application/pdf"
    assert base64.b64decode(att["contentBytes"]) == b"%PDF-1.4 letter"


async def test_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "outlook")
    get_settings.cache_clear()
    with pytest.raises(EmailNotSent, match="gmail or microsoft"):
        await notify.send(MAIL)
