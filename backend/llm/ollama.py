"""Ollama runs natively on the Mac (outside Docker) and serves the embedder."""

import httpx

from schemas.health import Check


async def ping_ollama(base_url: str, model: str) -> Check:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        return Check(ok=False, detail=f"Not reachable: {type(exc).__name__}")
    names = {m.get("name", "") for m in resp.json().get("models", [])}
    if not any(n == model or n.startswith(f"{model}:") for n in names):
        return Check(ok=False, detail=f"Running, but {model} is not pulled (ollama pull {model})")
    return Check(ok=True, detail=f"Running, {model} ready")
