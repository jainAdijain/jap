"""Sign-in and access control (PRD S1 to S9)."""

import re
from datetime import timedelta
from typing import Any

import httpx
import psycopg
import pytest

from auth import passwords, totp
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD

NEW_PASSWORD = "a different long one"
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"


# --- building blocks -------------------------------------------------------------------------


def test_totp_matches_rfc_6238_vectors() -> None:
    assert totp.matching_step(RFC_SECRET, "287082", None, at=59) == 1
    assert totp.matching_step(RFC_SECRET, "081804", None, at=1111111109) is not None
    assert totp.matching_step(RFC_SECRET, "000000", None, at=59) is None
    # A code is good once: steps at or before the last used one never match again.
    assert totp.matching_step(RFC_SECRET, "287082", 1, at=59) is None


async def test_password_hash_round_trip_at_full_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "N", 2**15)
    stored = await passwords.hash_password("correct horse battery")
    assert stored.startswith("scrypt$32768$8$1$")
    assert "correct horse" not in stored
    assert await passwords.verify_password("correct horse battery", stored)
    assert not await passwords.verify_password("correct horse batterY", stored)
    assert not await passwords.verify_password("x", "not a hash")


def _code(secret: str, offset: int = 0) -> str:
    return totp._code(secret, totp.now_step() + offset)


# --- everything is behind sign-in (S6) ----------------------------------------------------------

OPEN = {"/api/health", "/api/inngest"}


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)


async def test_every_route_but_health_and_sign_in_needs_a_session(anon: httpx.AsyncClient) -> None:
    from main import app

    checked = 0
    for path, methods in app.openapi()["paths"].items():
        if path in OPEN or path.startswith("/api/auth/"):
            continue
        for method in methods:
            resp = await anon.request(method.upper(), _concrete(path))
            assert resp.status_code == 401, f"{method} {path} answered {resp.status_code}"
            assert resp.json()["error"]["code"] == "signed_out"
            checked += 1
    assert checked > 30


async def test_auth_routes_that_need_a_session(anon: httpx.AsyncClient) -> None:
    for path in (
        "/api/auth/sign-out-everywhere",
        "/api/auth/password",
        "/api/auth/two-step/start",
        "/api/auth/two-step/confirm",
        "/api/auth/two-step/off",
        "/api/auth/two-step/recovery-codes",
    ):
        assert (await anon.post(path, json={})).status_code in (401, 422)
    assert (await anon.get("/api/auth/me")).status_code == 401


async def test_file_links_need_a_session(api: httpx.AsyncClient, anon: httpx.AsyncClient) -> None:
    """Resume, cover letter and screenshot links are /api routes too (S6)."""
    await api.post("/api/auth/sign-out")
    for path in (
        "/api/resumes/00000000-0000-0000-0000-000000000000/file",
        "/api/files/cover_letter/00000000-0000-0000-0000-000000000000",
    ):
        assert (await anon.get(path)).status_code == 401


# --- the first account (S1, step 1) -----------------------------------------------------------


async def test_first_account_is_the_admin_and_then_sign_up_closes(anon: httpx.AsyncClient) -> None:
    state = (await anon.get("/api/auth/state")).json()
    assert state == {
        "has_accounts": False, "sign_up_open": True, "user": None, "two_step_pending": False
    }  # fmt: skip
    short = await anon.post("/api/auth/sign-up", json={"email": OWNER_EMAIL, "password": "short"})
    assert short.status_code == 422 and short.json()["error"]["code"] == "weak_password"

    resp = await anon.post(
        "/api/auth/sign-up", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["email"] == OWNER_EMAIL
    assert resp.json()["user"]["is_admin"] is True
    assert (await anon.get("/api/profile")).status_code == 200

    again = await anon.post(
        "/api/auth/sign-up", json={"email": "someone@else.com", "password": OWNER_PASSWORD}
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "sign_up_closed"
    state = (await anon.get("/api/auth/state")).json()
    assert state["has_accounts"] and not state["sign_up_open"]
    assert state["user"]["email"] == OWNER_EMAIL


async def test_admin_email_setting_limits_the_first_account(
    anon: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import get_settings

    monkeypatch.setattr(get_settings(), "jap_admin_email", "me@example.com")
    wrong = await anon.post(
        "/api/auth/sign-up", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    assert wrong.status_code == 403
    ok = await anon.post(
        "/api/auth/sign-up", json={"email": "Me@Example.com", "password": OWNER_PASSWORD}
    )
    assert ok.status_code == 201


# --- sign in, lockout, sessions (S2, S5) --------------------------------------------------------


async def _signed_out(api: httpx.AsyncClient) -> httpx.AsyncClient:
    assert (await api.post("/api/auth/sign-out")).status_code == 204
    assert (await api.get("/api/profile")).status_code == 401
    return api


async def _sign_in(client: httpx.AsyncClient, password: str = OWNER_PASSWORD) -> httpx.Response:
    return await client.post(
        "/api/auth/sign-in", json={"email": OWNER_EMAIL.upper(), "password": password}
    )


async def test_sign_in_and_out(api: httpx.AsyncClient) -> None:
    client = await _signed_out(api)
    unknown = await client.post(
        "/api/auth/sign-in", json={"email": "who@example.com", "password": OWNER_PASSWORD}
    )
    wrong = await _sign_in(client, "not the password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()  # S2: the same answer for both
    ok = await _sign_in(client)
    assert ok.status_code == 200 and ok.json()["signed_in"] is True
    assert (await client.get("/api/profile")).status_code == 200


async def test_cookie_is_httponly_lax_and_host_only(api: httpx.AsyncClient) -> None:
    client = await _signed_out(api)
    cookie = (await _sign_in(client)).headers["set-cookie"].lower()
    assert cookie.startswith("jap_session=")
    assert "httponly" in cookie and "samesite=lax" in cookie and "domain" not in cookie


async def test_secure_cookie_on_https(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import get_settings

    client = await _signed_out(api)
    monkeypatch.setattr(get_settings(), "jap_cookie_secure", True)
    cookie = (await _sign_in(client)).headers["set-cookie"].lower()
    assert cookie.startswith("__host-jap_session=") and "secure" in cookie


async def test_five_wrong_passwords_lock_sign_in(api: httpx.AsyncClient) -> None:
    client = await _signed_out(api)
    for _ in range(5):
        assert (await _sign_in(client, "wrong password!")).status_code == 401
    locked = await _sign_in(client)
    assert locked.status_code == 429 and "15 min" in locked.json()["error"]["message"]


async def test_idle_and_old_sessions_end(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    sql.execute("UPDATE auth_session SET last_seen_at = now() - interval '8 days'")
    assert (await api.get("/api/profile")).status_code == 401
    await _sign_in(api)
    sql.execute("UPDATE auth_session SET expires_at = now() - interval '1 second'")
    assert (await api.get("/api/profile")).status_code == 401


async def test_sign_out_everywhere(api: httpx.AsyncClient, anon: httpx.AsyncClient) -> None:
    from main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as other:
        await _sign_in(other)
        assert (await other.get("/api/profile")).status_code == 200
        assert (await api.post("/api/auth/sign-out-everywhere")).status_code == 204
        assert (await other.get("/api/profile")).status_code == 401
        assert (await api.get("/api/profile")).status_code == 401


# --- password reset and change (S4, S7) -----------------------------------------------------------


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    sent: list[Any] = []

    async def fake_send(mail: Any) -> None:
        sent.append(mail)

    monkeypatch.setattr("api.auth.send_mail", fake_send)
    return sent


async def _reset(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post("/api/auth/reset", json={"token": token, "password": NEW_PASSWORD})


def _token(mail: Any) -> str:
    line = next(x for x in str(mail.body).splitlines() if "?reset=" in x)
    return line.split("?reset=")[1].strip()


async def test_password_reset_by_email(
    api: httpx.AsyncClient, outbox: list[Any], sql: psycopg.Connection[Any]
) -> None:
    client = await _signed_out(api)
    unknown = await client.post("/api/auth/forgot", json={"email": "who@example.com"})
    known = await client.post("/api/auth/forgot", json={"email": OWNER_EMAIL})
    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert len(outbox) == 1 and outbox[0].to == OWNER_EMAIL
    assert outbox[0].body.count("http://localhost:5173/?reset=") == 1
    token = _token(outbox[0])
    stored = sql.execute("SELECT count(*) FROM password_reset WHERE token_hash = %s", (token,))
    assert stored.fetchone() == (0,)  # only a hash of the link is kept

    # At most one email every 2 minutes.
    await client.post("/api/auth/forgot", json={"email": OWNER_EMAIL})
    assert len(outbox) == 1

    weak = await client.post("/api/auth/reset", json={"token": token, "password": "short"})
    assert weak.status_code == 422
    assert (await _reset(client, token)).status_code == 204
    again = await _reset(client, token)
    assert again.status_code == 400 and again.json()["error"]["code"] == "reset_link_expired"
    assert (await _sign_in(client)).status_code == 401
    assert (await _sign_in(client, NEW_PASSWORD)).status_code == 200


async def test_reset_link_expires_and_ends_sessions(
    api: httpx.AsyncClient, anon: httpx.AsyncClient, outbox: list[Any], sql: psycopg.Connection[Any]
) -> None:
    await api.post("/api/auth/forgot", json={"email": OWNER_EMAIL})
    token = _token(outbox[0])
    sql.execute("UPDATE password_reset SET expires_at = now() - interval '1 second'")
    assert (await _reset(api, token)).status_code == 400

    sql.execute("UPDATE password_reset SET created_at = now() - interval '3 minutes'")
    await api.post("/api/auth/forgot", json={"email": OWNER_EMAIL})
    token = _token(outbox[1])
    assert (await api.get("/api/profile")).status_code == 200
    assert (await _reset(api, token)).status_code == 204
    assert (await api.get("/api/profile")).status_code == 401  # every session ended


async def test_change_password_needs_the_current_one(api: httpx.AsyncClient) -> None:
    from main import app

    wrong = await api.post(
        "/api/auth/password", json={"current_password": "nope", "new_password": NEW_PASSWORD}
    )
    assert wrong.status_code == 403
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as other:
        await _sign_in(other)
        ok = await api.post(
            "/api/auth/password",
            json={"current_password": OWNER_PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert ok.status_code == 204
        assert (await api.get("/api/profile")).status_code == 200  # this browser stays in
        assert (await other.get("/api/profile")).status_code == 401  # the others are out


# --- two-step sign-in (S3) -----------------------------------------------------------------------


async def _turn_on_two_step(api: httpx.AsyncClient) -> tuple[str, list[str]]:
    setup = (await api.post("/api/auth/two-step/start")).json()
    assert setup["uri"].startswith("otpauth://totp/JAP:owner@example.com?secret=")
    assert setup["qr_svg"].startswith("<svg")
    secret = setup["secret"]
    bad = await api.post("/api/auth/two-step/confirm", json={"code": "000000"})
    assert bad.status_code == 422
    codes = (await api.post("/api/auth/two-step/confirm", json={"code": _code(secret)})).json()
    assert len(codes["recovery_codes"]) == 8
    me = (await api.get("/api/auth/me")).json()
    assert me == {
        "email": OWNER_EMAIL,
        "is_admin": True,
        "two_step": True,
        "recovery_codes_left": 8,
    }
    return secret, codes["recovery_codes"]


async def test_two_step_sign_in(api: httpx.AsyncClient) -> None:
    secret, _ = await _turn_on_two_step(api)
    client = await _signed_out(api)
    first = await _sign_in(client)
    assert first.json() == {"signed_in": False, "two_step": True, "user": None}
    assert (await client.get("/api/profile")).status_code == 401  # the password alone is not enough
    assert (await client.get("/api/auth/state")).json()["two_step_pending"] is True

    wrong = await client.post("/api/auth/sign-in/code", json={"code": "123456"})
    assert wrong.status_code == 401
    ok = await client.post("/api/auth/sign-in/code", json={"code": _code(secret, 1)})
    assert ok.status_code == 200 and ok.json()["signed_in"] is True
    assert (await client.get("/api/profile")).status_code == 200

    # The same code can't be used twice.
    await _signed_out(client)
    await _sign_in(client)
    replay = await client.post("/api/auth/sign-in/code", json={"code": _code(secret, 1)})
    assert replay.status_code == 401


async def test_code_step_without_a_password_first(anon: httpx.AsyncClient) -> None:
    resp = await anon.post("/api/auth/sign-in/code", json={"code": "123456"})
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "two_step_expired"


async def test_five_wrong_codes_need_the_password_again(api: httpx.AsyncClient) -> None:
    await _turn_on_two_step(api)
    client = await _signed_out(api)
    await _sign_in(client)
    for _ in range(5):
        wrong = await client.post("/api/auth/sign-in/code", json={"code": "000001"})
        assert wrong.status_code == 401
    gone = await client.post("/api/auth/sign-in/code", json={"code": "000001"})
    assert gone.json()["error"]["code"] in ("two_step_expired", "locked")


async def test_recovery_code_works_once(api: httpx.AsyncClient) -> None:
    _, codes = await _turn_on_two_step(api)
    client = await _signed_out(api)
    await _sign_in(client)
    ok = await client.post("/api/auth/sign-in/code", json={"code": codes[0].upper()})
    assert ok.status_code == 200
    assert (await client.get("/api/auth/me")).json()["recovery_codes_left"] == 7
    await _signed_out(client)
    await _sign_in(client)
    assert (await client.post("/api/auth/sign-in/code", json={"code": codes[0]})).status_code == 401


async def test_two_step_off_and_new_codes_need_the_password(api: httpx.AsyncClient) -> None:
    await _turn_on_two_step(api)
    codes_url, off_url = "/api/auth/two-step/recovery-codes", "/api/auth/two-step/off"
    assert (await api.post(codes_url, json={"password": "nope"})).status_code == 403
    fresh = await api.post(codes_url, json={"password": OWNER_PASSWORD})
    assert len(fresh.json()["recovery_codes"]) == 8
    assert (await api.post(off_url, json={"password": "nope"})).status_code == 403
    assert (await api.post(off_url, json={"password": OWNER_PASSWORD})).status_code == 204
    client = await _signed_out(api)
    assert (await _sign_in(client)).json()["signed_in"] is True


# --- internal endpoints and other sites (S8, S9) ---------------------------------------------


@pytest.mark.parametrize(
    "header", ["X-Forwarded-For", "Forwarded", "Cf-Connecting-Ip", "X-Real-Ip", "Cf-Ray"]
)
async def test_inngest_endpoint_refuses_proxied_requests(
    anon: httpx.AsyncClient, header: str
) -> None:
    resp = await anon.put("/api/inngest", headers={header: "203.0.113.9"})
    assert resp.status_code == 404


async def test_inngest_endpoint_answers_inside_docker(anon: httpx.AsyncClient) -> None:
    resp = await anon.get("/api/inngest")
    assert resp.status_code != 404


async def test_requests_from_other_sites_are_refused(api: httpx.AsyncClient) -> None:
    other = await api.post("/api/auth/sign-out", headers={"Origin": "https://evil.example"})
    assert other.status_code == 403 and other.json()["error"]["code"] == "bad_origin"
    assert (await api.get("/api/profile")).status_code == 200
    ours = await api.post("/api/auth/sign-out", headers={"Origin": "http://localhost:5173"})
    assert ours.status_code == 204


async def test_a_turned_off_account_cannot_sign_in(
    api: httpx.AsyncClient, sql: psycopg.Connection[Any]
) -> None:
    sql.execute("UPDATE app_user SET disabled_at = now()")
    assert (await api.get("/api/profile")).status_code == 401
    resp = await _sign_in(api)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "account_off"


def test_sessions_last_seven_days_idle() -> None:
    from config import get_settings

    settings = get_settings()
    assert timedelta(days=settings.jap_session_idle_days) == timedelta(days=7)
    assert settings.jap_session_max_days == 30
