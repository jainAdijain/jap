"""The one Inngest client. INNGEST_DEV=1 (dev) points it at the Inngest dev server; test and
prod run a self-hosted `inngest start` with INNGEST_DEV=0, INNGEST_BASE_URL and their own
INNGEST_SIGNING_KEY and INNGEST_EVENT_KEY (H3). The SDK reads all of these from the environment."""

import os

import httpx
import inngest

from schemas.health import Check

# Unset INNGEST_DEV keeps dev mode, as before; only an explicit 0 or false is production.
inngest_client = inngest.Inngest(
    app_id="jap", is_production=os.getenv("INNGEST_DEV", "1").lower() in ("0", "false")
)


async def ping_inngest(base_url: str) -> Check:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/health")
    except httpx.HTTPError as exc:
        return Check(ok=False, detail=f"Not reachable: {type(exc).__name__}")
    if resp.status_code >= 500:
        return Check(ok=False, detail=f"Inngest answered {resp.status_code}")
    return Check(ok=True, detail="Reachable")
