"""Microsoft 365 sender (E4): JAP's emails from a Microsoft mailbox through Microsoft Graph.

Microsoft 365 no longer takes SMTP passwords, so JAP signs in once with OAuth (the device
code flow, `make email-login`) and keeps the token in data/microsoft-token.json. The access
token lasts about an hour; when a send finds it expired, the refresh token buys a new pair
(Microsoft replaces the refresh token on each use, and it lapses after 90 days unused).
Nothing renews in the background. Mail goes out with POST /me/sendMail, delegated Mail.Send.
"""

import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from config import get_settings
from pipeline.notify import EmailNotSent, Mail

SCOPE = "offline_access https://graph.microsoft.com/Mail.Send"
GRAPH_SEND = "https://graph.microsoft.com/v1.0/me/sendMail"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# Renew a little before the access token runs out, so a send never races its expiry.
EARLY_SECONDS = 120
SIGN_IN_AGAIN = "Microsoft sign-in has expired or was revoked. Run `make email-login` again."


def token_path() -> Path:
    return Path(get_settings().jap_data_dir) / "microsoft-token.json"


def _authority() -> str:
    return f"https://login.microsoftonline.com/{get_settings().ms_tenant_id}/oauth2/v2.0"


def _client_id() -> str:
    client_id = get_settings().ms_client_id
    if not client_id:
        raise EmailNotSent("MS_CLIENT_ID is not set in .env")
    return client_id


def _save(token: dict[str, Any]) -> dict[str, Any]:
    saved = {
        "access_token": token["access_token"],
        "refresh_token": token["refresh_token"],
        "expires_at": int(time.time()) + int(token.get("expires_in", 3600)),
    }
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved))
    path.chmod(0o600)
    return saved


def _load() -> dict[str, Any]:
    path = token_path()
    if not path.exists():
        raise EmailNotSent("Not signed in to Microsoft. Run `make email-login` first.")
    data: dict[str, Any] = json.loads(path.read_text())
    return data


async def access_token(client: httpx.AsyncClient) -> str:
    """A valid access token, renewed with the refresh token when it has expired."""
    token = _load()
    if int(token.get("expires_at", 0)) - EARLY_SECONDS > time.time():
        return str(token["access_token"])
    resp = await client.post(
        f"{_authority()}/token",
        data={
            "client_id": _client_id(),
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "scope": SCOPE,
        },
    )
    if resp.status_code != 200:
        error = resp.json().get("error", "") if resp.content else ""
        if error in ("invalid_grant", "interaction_required"):
            raise EmailNotSent(SIGN_IN_AGAIN)
        raise EmailNotSent(f"Microsoft token renewal failed: {resp.status_code} {resp.text[:200]}")
    return str(_save(resp.json())["access_token"])


def message(mail: Mail) -> dict[str, Any]:
    """The Graph sendMail body for a Mail."""
    msg: dict[str, Any] = {
        "subject": mail.subject,
        "body": {"contentType": "Text", "content": mail.body},
        "toRecipients": [{"emailAddress": {"address": mail.to}}],
    }
    if mail.attachment is not None:
        msg["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": mail.attachment_name,
                "contentType": mail.attachment_type,
                "contentBytes": base64.b64encode(mail.attachment.read_bytes()).decode(),
            }
        ]
    return {"message": msg, "saveToSentItems": True}


async def send(mail: Mail) -> None:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            token = await access_token(client)
            resp = await client.post(
                GRAPH_SEND, json=message(mail), headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError as exc:
        raise EmailNotSent(f"Microsoft Graph failed: {exc}") from exc
    if resp.status_code == 401:
        raise EmailNotSent(SIGN_IN_AGAIN)
    if resp.status_code != 202:
        raise EmailNotSent(f"Microsoft Graph sendMail: {resp.status_code} {resp.text[:200]}")


async def login() -> None:
    """The one-time sign-in: print a code, wait while it is entered in a browser, save tokens."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_authority()}/devicecode", data={"client_id": _client_id(), "scope": SCOPE}
        )
        resp.raise_for_status()
        flow = resp.json()
        print(flow["message"], flush=True)
        interval = int(flow.get("interval", 5))
        deadline = time.time() + int(flow.get("expires_in", 900))
        while time.time() < deadline:
            await asyncio.sleep(interval)
            resp = await client.post(
                f"{_authority()}/token",
                data={
                    "client_id": _client_id(),
                    "grant_type": DEVICE_GRANT,
                    "device_code": flow["device_code"],
                },
            )
            body = resp.json()
            if resp.status_code == 200:
                _save(body)
                print(f"Signed in. Token saved to {token_path()}.")
                return
            if body.get("error") == "slow_down":
                interval += 5
            elif body.get("error") != "authorization_pending":
                raise SystemExit(f"Sign-in failed: {body.get('error_description', body)}")
    raise SystemExit("Sign-in timed out. Run `make email-login` again.")


if __name__ == "__main__":
    if sys.argv[1:] != ["login"]:
        raise SystemExit("usage: python -m pipeline.microsoft login")
    asyncio.run(login())
