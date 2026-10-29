"""OpenRouter chat client. The only module that talks to OpenRouter (a CI test checks this).

Call it through llm.call(), never directly, so every request is redacted and guarded first.
"""

import json
from dataclasses import dataclass
from typing import Any

import httpx

from config import get_settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class ModelCallError(Exception):
    """OpenRouter refused the request or answered with something unusable."""


class MissingApiKey(ModelCallError):
    """OPENROUTER_API_KEY is empty; retrying will not help."""


@dataclass
class ChatResult:
    content: str
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None


def build_body(
    model: str, system: str, user: str, schema: dict[str, Any], temperature: float
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "output", "strict": True, "schema": schema},
        },
        # PRD Privacy: only providers that do not log or train on prompts.
        "provider": {"data_collection": "deny"},
        "usage": {"include": True},
    }


async def chat(api_key: str, body: dict[str, Any], wait_s: float = 120.0) -> ChatResult:
    if not api_key:
        raise MissingApiKey("OPENROUTER_API_KEY is not set in .env")
    async with httpx.AsyncClient(timeout=wait_s) as client:
        resp = await client.post(
            get_settings().openrouter_url or OPENROUTER_URL,
            content=json.dumps(body),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "JAP",
            },
        )
    if resp.status_code >= 400:
        try:
            message = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            message = resp.text
        raise ModelCallError(f"OpenRouter {resp.status_code}: {str(message)[:300]}")
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelCallError("OpenRouter answered without a message") from exc
    usage = data.get("usage") or {}
    return ChatResult(
        content=content or "",
        tokens_in=usage.get("prompt_tokens"),
        tokens_out=usage.get("completion_tokens"),
        cost_usd=usage.get("cost"),
    )
