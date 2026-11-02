"""The one Inngest client. INNGEST_DEV and INNGEST_BASE_URL point it at the dev server."""

import logging

import httpx
import inngest

from observability.inngest_mw import LogMiddleware
from schemas.health import Check

# LogMiddleware: the run, application and Inngest ids on every log line, one line per step.
inngest_client = inngest.Inngest(
    app_id="jap",
    is_production=False,
    middleware=[LogMiddleware],
    logger=logging.getLogger("inngest"),
)


async def ping_inngest(base_url: str) -> Check:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/health")
    except httpx.HTTPError as exc:
        return Check(ok=False, detail=f"Not reachable: {type(exc).__name__}")
    if resp.status_code >= 500:
        return Check(ok=False, detail=f"Dev server answered {resp.status_code}")
    return Check(ok=True, detail="Dev server reachable")
